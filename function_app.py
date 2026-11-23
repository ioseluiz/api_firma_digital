"""Azure Functions (modelo v2, Python) — API de firma para la plataforma INI Firma.

Rutas:
  POST /api/v1/firmar   recibe PDF + firmante + posición; devuelve PDF firmado + hashes
  GET  /api/v1/salud    versión, origen de imágenes y si la imagen por defecto es accesible

Seguridad: en producción va detrás de Easy Auth (Entra ID, rol Firma.Ejecutar). Mientras tanto,
`AuthLevel.FUNCTION` exige la clave de función en `x-functions-key`. Ver README.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import azure.functions as func
from pydantic import ValidationError

from firma import VERSION
from firma.errores import ErrorFirma, PdfDemasiadoGrande, PdfInvalido, PeticionInvalida
from firma.estampar import TEXTO_SELLO_POR_DEFECTO, ParametrosEstampado, estampar
from firma.imagenes import OrigenImagenes, origen_desde_entorno
from firma.modelos import ErrorResponse, FirmaRequest, FirmaResponse, SaludResponse

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)
log = logging.getLogger("ini.firma")

_origen: Optional[OrigenImagenes] = None


def obtener_origen() -> OrigenImagenes:
    global _origen
    if _origen is None:
        _origen = origen_desde_entorno()
    return _origen


def reiniciar_origen() -> None:
    """Para pruebas: fuerza a releer las variables de entorno."""
    global _origen
    _origen = None


def _max_pdf_bytes() -> int:
    return int(os.getenv("FIRMA_MAX_PDF_MB", "25")) * 1024 * 1024


def _json(cuerpo: dict, estado: int, correlation_id: str) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(cuerpo, ensure_ascii=False),
        status_code=estado,
        mimetype="application/json",
        headers={"X-Correlation-Id": correlation_id},
    )


def _error(e: ErrorFirma, correlation_id: str) -> func.HttpResponse:
    cuerpo = ErrorResponse(error=e.codigo, mensaje=e.mensaje, correlation_id=correlation_id).model_dump()
    return _json(cuerpo, e.http, correlation_id)


# "%PDF-" codificado en base64. Si tras decodificar aparece esto, el flujo codificó dos veces.
_PDF_EN_BASE64 = b"JVBERi0"


def _b64(texto, mensaje: str) -> bytes:
    try:
        return base64.b64decode(b"".join(texto.split()) if isinstance(texto, bytes) else texto, validate=True)
    except (binascii.Error, ValueError) as e:
        raise PdfInvalido(mensaje) from e


def _decodificar_pdf(b64: str) -> bytes:
    """Decodifica `pdf_base64` tolerando lo que produce `base64()` en Power Automate sobre un
    `contentBytes` que ya era base64 (desencadenador manual):

    - base64 doble: el primer decodificado es texto que empieza por `JVBERi0` (`%PDF-`);
    - base64 del objeto binario: el primer decodificado es el JSON
      `{"$content-type": ..., "$content": "<base64>"}`.

    En ambos casos se decodifica otra vez. Un PDF real nunca empieza por `{` ni por `J`.
    """
    pdf = _b64(b64, "pdf_base64 no es base64 válido.")
    cuerpo = pdf.lstrip()
    if cuerpo.startswith(b"{"):
        try:
            objeto = json.loads(cuerpo.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise PdfInvalido("pdf_base64 decodifica a texto que empieza por '{' pero no es JSON.") from e
        interno = objeto.get("$content") if isinstance(objeto, dict) else None
        if not isinstance(interno, str):
            raise PdfInvalido("pdf_base64 decodifica a un objeto JSON sin '$content'.")
        pdf = _b64("".join(interno.split()), "pdf_base64 trae un objeto cuyo '$content' no es base64 válido.")
    elif cuerpo.startswith(_PDF_EN_BASE64):
        pdf = _b64(cuerpo, "pdf_base64 parece base64 doble, pero el contenido interno no es base64 válido.")
    return pdf


def _resumen_validacion(err: ValidationError) -> str:
    partes = []
    for x in err.errors()[:6]:
        ruta = ".".join(str(p) for p in x.get("loc", ())) or "(raíz)"
        partes.append(f"{ruta}: {x.get('msg')}")
    return "; ".join(partes)


@app.route(route="v1/salud", methods=["GET"])
def salud(req: func.HttpRequest) -> func.HttpResponse:
    correlation_id = req.headers.get("X-Correlation-Id") or str(uuid.uuid4())
    imagen = os.getenv("FIRMAS_IMAGEN_POR_DEFECTO", "gerente.png")
    try:
        origen = obtener_origen()
    except Exception as e:  # origen mal configurado
        log.exception("salud: origen de imágenes no disponible")
        cuerpo = ErrorResponse(error="origen_no_disponible", mensaje=str(e), correlation_id=correlation_id).model_dump()
        return _json(cuerpo, 503, correlation_id)
    try:
        origen.obtener(imagen)
        ok_img = True
    except Exception:
        ok_img = False
    cuerpo = SaludResponse(
        ok=ok_img,
        version=VERSION,
        origen_imagenes=origen.nombre,
        imagen_por_defecto=imagen,
        imagen_por_defecto_ok=ok_img,
        hora_servidor_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    ).model_dump()
    return _json(cuerpo, 200 if ok_img else 503, correlation_id)


@app.route(route="v1/firmar", methods=["POST"])
def firmar(req: func.HttpRequest) -> func.HttpResponse:
    correlation_id = req.headers.get("X-Correlation-Id") or str(uuid.uuid4())
    t0 = time.perf_counter()
    codigo = "?"
    try:
        try:
            cuerpo = req.get_json()
        except ValueError as e:
            raise PeticionInvalida("El cuerpo no es JSON válido.") from e
        if not isinstance(cuerpo, dict):
            raise PeticionInvalida("El cuerpo debe ser un objeto JSON.")

        try:
            peticion = FirmaRequest.model_validate(cuerpo)
        except ValidationError as e:
            raise PeticionInvalida(_resumen_validacion(e)) from e
        codigo = peticion.codigo

        maximo = _max_pdf_bytes()
        # Tamaño antes de decodificar: base64 ocupa 4/3 del binario.
        if len(peticion.pdf_base64) * 3 // 4 > maximo:
            raise PdfDemasiadoGrande(f"El PDF supera el máximo de {maximo // (1024 * 1024)} MB.")
        pdf = _decodificar_pdf(peticion.pdf_base64)
        if len(pdf) > maximo:
            raise PdfDemasiadoGrande(f"El PDF supera el máximo de {maximo // (1024 * 1024)} MB.")

        imagen = obtener_origen().obtener(peticion.firmante.imagen_id)

        params = ParametrosEstampado(
            codigo=peticion.codigo,
            fecha_firma=peticion.fecha(),
            firmante_nombre=peticion.firmante.nombre,
            firmante_cargo=peticion.firmante.cargo,
            pagina=peticion.posicion.pagina,
            x_mm=peticion.posicion.x_mm,
            y_mm=peticion.posicion.y_mm,
            ancho_mm=peticion.posicion.ancho_mm,
            ancla_texto=peticion.posicion.ancla_texto,
            ancla_alineacion=peticion.posicion.ancla_alineacion,
            si_no_hay_ancla=peticion.posicion.si_no_hay_ancla,
            sello_texto=peticion.sello.texto,
            sello_codigo=peticion.sello.codigo_verificacion,
            plantilla_sello=os.getenv("FIRMA_TEXTO_SELLO", TEXTO_SELLO_POR_DEFECTO),
        )
        resultado = estampar(pdf, imagen, params)

        duracion_ms = int((time.perf_counter() - t0) * 1000)
        respuesta = FirmaResponse(
            codigo=peticion.codigo,
            pdf_firmado_base64=base64.b64encode(resultado.pdf).decode("ascii"),
            sha256=resultado.sha256,
            codigo_verificacion=resultado.codigo_verificacion,
            paginas=resultado.paginas,
            pagina_firmada=resultado.pagina_firmada,
            ancla_encontrada=resultado.ancla_encontrada,
            firmante=peticion.firmante.nombre,
            fecha_firma=peticion.fecha_firma,
            duracion_ms=duracion_ms,
        )
        # Nunca se registra el contenido ni el nombre del documento.
        log.info(
            "firmar ok codigo=%s solicitud=%s bytes_in=%d bytes_out=%d paginas=%d pagina=%d ancla=%s ms=%d corr=%s",
            peticion.codigo, peticion.solicitud_id, len(pdf), len(resultado.pdf), resultado.paginas,
            resultado.pagina_firmada, resultado.ancla_encontrada, duracion_ms, correlation_id,
        )
        if "application/pdf" in (req.headers.get("Accept") or ""):
            # Para pruebas manuales (Postman "Send and Download", curl -o): el PDF como archivo,
            # con los datos de la respuesta JSON en cabeceras. El flujo usa siempre el JSON.
            return func.HttpResponse(
                resultado.pdf,
                status_code=200,
                mimetype="application/pdf",
                headers={
                    "X-Correlation-Id": correlation_id,
                    "Content-Disposition": f'attachment; filename="{peticion.codigo}_firmado.pdf"',
                    "X-Sha256": resultado.sha256,
                    "X-Codigo-Verificacion": resultado.codigo_verificacion,
                    "X-Paginas": str(resultado.paginas),
                    "X-Pagina-Firmada": str(resultado.pagina_firmada),
                    "X-Ancla-Encontrada": str(resultado.ancla_encontrada).lower(),
                    "X-Duracion-Ms": str(duracion_ms),
                },
            )
        return _json(respuesta.model_dump(), 200, correlation_id)

    except ErrorFirma as e:
        log.warning(
            "firmar rechazado codigo=%s error=%s http=%d msg=%s corr=%s",
            codigo, e.codigo, e.http, e.mensaje, correlation_id,
        )
        return _error(e, correlation_id)
    except Exception:
        log.exception("firmar error interno codigo=%s corr=%s", codigo, correlation_id)
        return _error(
            ErrorFirma("Error interno al firmar; busque el correlation_id en Application Insights."),
            correlation_id,
        )
