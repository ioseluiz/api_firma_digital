"""Azure Functions (modelo v2, Python) — API de firma para la plataforma INI Firma.

Rutas:
  POST   /api/v1/firmar           recibe PDF + firmante + posición; devuelve PDF firmado + hashes
  POST   /api/v1/visor            guarda el original en el contenedor del visor; devuelve URL SAS temporal
  GET    /api/v1/visor/{codigo}   URL SAS nueva para un original ya publicado
  DELETE /api/v1/visor/{codigo}   borra el original del visor (idempotente)
  GET    /api/v1/visor/{codigo}/abrir/{token}   ANÓNIMA: sirve el PDF (con CORS); es la URL que guarda SharePoint
  GET    /api/v1/visor/{codigo}/pagina/{n}/{token}   ANÓNIMA: la página n como PNG (para elegir con un clic dónde va la firma)
  GET    /api/v1/visor/{codigo}/info/{token}    ANÓNIMA: número de páginas y tamaño en mm de cada una
  GET    /api/v1/salud            versión, origen de imágenes, visor y si la imagen por defecto es accesible

Seguridad: en producción va detrás de Easy Auth (Entra ID, rol Firma.Ejecutar). Mientras tanto,
`AuthLevel.FUNCTION` exige la clave de función en `x-functions-key`. Ver README.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import azure.functions as func
from pydantic import ValidationError

from firma import VERSION
from firma.errores import ErrorFirma, PdfDemasiadoGrande, PdfInvalido, PeticionInvalida, VisorNoEncontrado
from firma.estampar import PT_POR_MM, TEXTO_SELLO_POR_DEFECTO, ParametrosEstampado, estampar, rect_pagina_en_lienzo
from firma.imagenes import OrigenImagenes, origen_desde_entorno
from firma.modelos import (
    lienzo_a_tupla,
    CODIGO_PATRON, ErrorResponse, FirmaRequest, FirmaResponse, PaginaInfo, SaludResponse,
    VisorBorradoResponse, VisorInfoResponse, VisorRequest, VisorResponse,
)
from firma.visor import AlmacenVisor, almacen_desde_entorno, token_valido, url_corta

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


_visor: Optional[AlmacenVisor] = None
_CODIGO_RE = re.compile(CODIGO_PATRON)


def obtener_visor() -> AlmacenVisor:
    global _visor
    if _visor is None:
        _visor = almacen_desde_entorno()
    return _visor


def reiniciar_visor() -> None:
    """Para pruebas: fuerza a releer las variables de entorno."""
    global _visor
    _visor = None


def _base_api(req: func.HttpRequest) -> str:
    """`https://host/api/v1` a partir de la petición (respeta el host del proxy)."""
    from urllib.parse import urlsplit

    u = urlsplit(req.url)
    host = req.headers.get("X-Forwarded-Host") or u.netloc
    esquema = u.scheme if ("localhost" in host or "127.0.0.1" in host) else "https"
    return f"{esquema}://{host}/api/v1"


def _respuesta_visor(req: func.HttpRequest, codigo: str, r) -> dict:
    secreto = os.getenv("VISOR_SECRETO", "")
    url = url_corta(_base_api(req), codigo, secreto) if secreto else r.url
    return VisorResponse(codigo=codigo, url_visor=url, url_sas=r.url, vence_utc=r.vence_utc.isoformat(),
                         blob=r.blob, bytes=r.bytes, dias_vigencia=r.dias).model_dump()


def _codigo_de_ruta(req: func.HttpRequest) -> str:
    codigo = (req.route_params.get("codigo") or "").strip()
    if not codigo or len(codigo) > 40 or not _CODIGO_RE.match(codigo):
        raise PeticionInvalida("El código de la ruta no es válido.")
    return codigo


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
    try:
        visor = obtener_visor().nombre
    except Exception as e:  # visor mal configurado: no tumba la salud, se reporta
        log.exception("salud: visor no disponible")
        visor = f"error: {e}"
    cuerpo = SaludResponse(
        ok=ok_img,
        version=VERSION,
        origen_imagenes=origen.nombre,
        visor=visor,
        imagen_por_defecto=imagen,
        imagen_por_defecto_ok=ok_img,
        hora_servidor_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    ).model_dump()
    return _json(cuerpo, 200 if ok_img else 503, correlation_id)


@app.route(route="v1/visor", methods=["POST"])
def visor_publicar(req: func.HttpRequest) -> func.HttpResponse:
    correlation_id = req.headers.get("X-Correlation-Id") or str(uuid.uuid4())
    codigo = "?"
    try:
        try:
            cuerpo = req.get_json()
        except ValueError as e:
            raise PeticionInvalida("El cuerpo no es JSON válido.") from e
        if not isinstance(cuerpo, dict):
            raise PeticionInvalida("El cuerpo debe ser un objeto JSON.")
        try:
            peticion = VisorRequest.model_validate(cuerpo)
        except ValidationError as e:
            raise PeticionInvalida(_resumen_validacion(e)) from e
        codigo = peticion.codigo

        maximo = _max_pdf_bytes()
        if len(peticion.pdf_base64) * 3 // 4 > maximo:
            raise PdfDemasiadoGrande(f"El PDF supera el máximo de {maximo // (1024 * 1024)} MB.")
        pdf = _decodificar_pdf(peticion.pdf_base64)
        if len(pdf) > maximo:
            raise PdfDemasiadoGrande(f"El PDF supera el máximo de {maximo // (1024 * 1024)} MB.")
        if not pdf.lstrip().startswith(b"%PDF"):
            raise PdfInvalido("El archivo no es un PDF (no empieza por %PDF).")

        r = obtener_visor().publicar(peticion.codigo, pdf)
        log.info("visor publicado codigo=%s solicitud=%s bytes=%d vence=%s corr=%s",
                 peticion.codigo, peticion.solicitud_id, r.bytes, r.vence_utc.isoformat(), correlation_id)
        return _json(_respuesta_visor(req, peticion.codigo, r), 200, correlation_id)
    except ErrorFirma as e:
        log.warning("visor rechazado codigo=%s error=%s http=%d msg=%s corr=%s", codigo, e.codigo, e.http, e.mensaje, correlation_id)
        return _error(e, correlation_id)
    except Exception:
        log.exception("visor error interno codigo=%s corr=%s", codigo, correlation_id)
        return _error(ErrorFirma("Error interno al publicar el visor; busque el correlation_id en Application Insights."), correlation_id)


@app.route(route="v1/visor/{codigo}", methods=["GET", "DELETE"])
def visor_codigo(req: func.HttpRequest) -> func.HttpResponse:
    correlation_id = req.headers.get("X-Correlation-Id") or str(uuid.uuid4())
    codigo = "?"
    try:
        codigo = _codigo_de_ruta(req)
        visor = obtener_visor()
        if req.method.upper() == "DELETE":
            borrado = visor.borrar(codigo)
            log.info("visor borrado codigo=%s existia=%s corr=%s", codigo, borrado, correlation_id)
            return _json(VisorBorradoResponse(codigo=codigo, borrado=borrado).model_dump(), 200, correlation_id)
        r = visor.renovar(codigo)
        log.info("visor renovado codigo=%s vence=%s corr=%s", codigo, r.vence_utc.isoformat(), correlation_id)
        return _json(_respuesta_visor(req, codigo, r), 200, correlation_id)
    except ErrorFirma as e:
        log.warning("visor rechazado codigo=%s error=%s http=%d msg=%s corr=%s", codigo, e.codigo, e.http, e.mensaje, correlation_id)
        return _error(e, correlation_id)
    except Exception:
        log.exception("visor error interno codigo=%s corr=%s", codigo, correlation_id)
        return _error(ErrorFirma("Error interno en el visor; busque el correlation_id en Application Insights."), correlation_id)


def _cors() -> dict:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Max-Age": "3600",
    }


@app.route(route="v1/visor/{codigo}/abrir/{token}", methods=["GET", "HEAD", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
def visor_abrir(req: func.HttpRequest) -> func.HttpResponse:
    """Ruta anónima: el control PDF de Power Apps (y cualquier navegador) la abre sin clave. El token HMAC por
    código impide adivinar URLs. Sirve los bytes directamente (no redirige): el control los descarga desde el
    navegador y una redirección hacia Blob exige CORS en dos saltos; aquí basta con la cabecera de esta respuesta."""
    correlation_id = req.headers.get("X-Correlation-Id") or str(uuid.uuid4())
    codigo = "?"
    try:
        codigo = _codigo_de_ruta(req)
        secreto = os.getenv("VISOR_SECRETO", "")
        if not token_valido(codigo, req.route_params.get("token", ""), secreto):
            log.warning("visor abrir rechazado codigo=%s corr=%s", codigo, correlation_id)
            return func.HttpResponse("No encontrado.", status_code=404, mimetype="text/plain")
        if req.method.upper() == "OPTIONS":
            return func.HttpResponse("", status_code=204, headers=_cors())
        pdf = obtener_visor().leer(codigo)
        log.info("visor abrir codigo=%s bytes=%d corr=%s", codigo, len(pdf), correlation_id)
        if req.method.upper() == "HEAD":
            pdf = b""
        return func.HttpResponse(
            pdf, status_code=200, mimetype="application/pdf",
            headers={
                **_cors(),
                "Content-Disposition": f'inline; filename="{codigo}.pdf"',
                "Cache-Control": "private, max-age=300",
                "X-Correlation-Id": correlation_id,
            },
        )
    except ErrorFirma as e:
        log.warning("visor abrir error codigo=%s error=%s corr=%s", codigo, e.codigo, correlation_id)
        return func.HttpResponse("No encontrado." if e.http == 404 else "Visor no disponible.", status_code=e.http, mimetype="text/plain")
    except Exception:
        log.exception("visor abrir error interno codigo=%s corr=%s", codigo, correlation_id)
        return func.HttpResponse("Visor no disponible.", status_code=500, mimetype="text/plain")


def _visor_anonimo(req: func.HttpRequest, correlation_id: str):
    """Valida código y token de una ruta anónima del visor; devuelve (codigo, pdf) o una respuesta de error."""
    codigo = _codigo_de_ruta(req)
    if not token_valido(codigo, req.route_params.get("token", ""), os.getenv("VISOR_SECRETO", "")):
        log.warning("visor ruta anónima rechazada codigo=%s corr=%s", codigo, correlation_id)
        return codigo, None, func.HttpResponse("No encontrado.", status_code=404, mimetype="text/plain")
    return codigo, obtener_visor().leer(codigo), None


@app.route(route="v1/visor/{codigo}/info/{token}", methods=["GET", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
def visor_info(req: func.HttpRequest) -> func.HttpResponse:
    correlation_id = req.headers.get("X-Correlation-Id") or str(uuid.uuid4())
    codigo = "?"
    try:
        if req.method.upper() == "OPTIONS":
            return func.HttpResponse("", status_code=204, headers=_cors())
        codigo, pdf, error = _visor_anonimo(req, correlation_id)
        if error:
            return error
        import pymupdf

        with pymupdf.open(stream=pdf, filetype="pdf") as doc:
            tamanos = [PaginaInfo(numero=i + 1, ancho_mm=round(p.rect.width / 72 * 25.4, 1), alto_mm=round(p.rect.height / 72 * 25.4, 1)) for i, p in enumerate(doc)]
        cuerpo = VisorInfoResponse(codigo=codigo, paginas=len(tamanos), tamanos=tamanos).model_dump()
        return func.HttpResponse(json.dumps(cuerpo, ensure_ascii=False), status_code=200, mimetype="application/json",
                                 headers={**_cors(), "Cache-Control": "private, max-age=300", "X-Correlation-Id": correlation_id})
    except ErrorFirma as e:
        return func.HttpResponse("No encontrado." if e.http == 404 else "Visor no disponible.", status_code=e.http, mimetype="text/plain", headers=_cors())
    except Exception:
        log.exception("visor info error interno codigo=%s corr=%s", codigo, correlation_id)
        return func.HttpResponse("Visor no disponible.", status_code=500, mimetype="text/plain", headers=_cors())


@app.route(route="v1/visor/{codigo}/pagina/{numero}/{token}", methods=["GET", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
def visor_pagina(req: func.HttpRequest) -> func.HttpResponse:
    """La página pedida como PNG (ancho ~1000 px): la app la muestra en un control Imagen con una cuadrícula
    encima; como la imagen no tiene desplazamiento interno, el clic se traduce a % de la página sin ambigüedad."""
    correlation_id = req.headers.get("X-Correlation-Id") or str(uuid.uuid4())
    codigo = "?"
    try:
        if req.method.upper() == "OPTIONS":
            return func.HttpResponse("", status_code=204, headers=_cors())
        codigo, pdf, error = _visor_anonimo(req, correlation_id)
        if error:
            return error
        try:
            numero = int(req.route_params.get("numero", "0"))
        except ValueError:
            numero = 0
        import pymupdf

        lienzo = lienzo_a_tupla(req.params.get("lienzo")) if re.fullmatch(r"[1-9]\d{0,4}x[1-9]\d{0,4}", req.params.get("lienzo") or "") else None
        with pymupdf.open(stream=pdf, filetype="pdf") as doc:
            if numero == 0:
                numero = doc.page_count
            if numero < 1 or numero > doc.page_count:
                return func.HttpResponse("Página fuera de rango.", status_code=404, mimetype="text/plain", headers=_cors())
            page = doc[numero - 1]
            if lienzo:
                # La página ajustada y centrada en un lienzo de esa proporción, con margen gris: la app pone la
                # imagen en un control de la misma proporción (estirada) y el clic se mide en % del lienzo.
                with pymupdf.open() as hoja:
                    pg = hoja.new_page(width=lienzo[0], height=lienzo[1])
                    pg.draw_rect(pg.rect, color=None, fill=(0.90, 0.90, 0.90))
                    destino = rect_pagina_en_lienzo(page.rect.width, page.rect.height, lienzo[0], lienzo[1])
                    pg.draw_rect(destino, color=None, fill=(1, 1, 1))  # el fondo de un PDF es transparente: sin esto se vería gris
                    pg.show_pdf_page(destino, doc, numero - 1)
                    escala = 1000.0 / lienzo[0]
                    png = pg.get_pixmap(matrix=pymupdf.Matrix(escala, escala), alpha=False).tobytes("png")
            else:
                escala = 1000.0 / max(page.rect.width, 1)
                png = page.get_pixmap(matrix=pymupdf.Matrix(escala, escala), alpha=False).tobytes("png")
        log.info("visor pagina codigo=%s n=%d bytes=%d corr=%s", codigo, numero, len(png), correlation_id)
        return func.HttpResponse(png, status_code=200, mimetype="image/png",
                                 headers={**_cors(), "Cache-Control": "private, max-age=300", "X-Correlation-Id": correlation_id})
    except ErrorFirma as e:
        return func.HttpResponse("No encontrado." if e.http == 404 else "Visor no disponible.", status_code=e.http, mimetype="text/plain", headers=_cors())
    except Exception:
        log.exception("visor pagina error interno codigo=%s corr=%s", codigo, correlation_id)
        return func.HttpResponse("Visor no disponible.", status_code=500, mimetype="text/plain", headers=_cors())


_MAX_IMAGEN_BYTES = 2 * 1024 * 1024


def _decodificar_imagen(b64: str) -> bytes:
    """PNG o JPEG en base64 (acepta el objeto $content del conector, como el PDF)."""
    if isinstance(b64, dict):
        b64 = b64.get("$content", "")
    try:
        datos = base64.b64decode(b64, validate=False)
    except Exception as e:
        raise PeticionInvalida("firmante.imagen_base64 no es base64 válido.") from e
    if len(datos) > _MAX_IMAGEN_BYTES:
        raise PeticionInvalida("La imagen de firma supera los 2 MB.")
    if not (datos.startswith(b"\x89PNG\r\n\x1a\n") or datos.startswith(b"\xff\xd8\xff")):
        raise PeticionInvalida("La imagen de firma debe ser PNG o JPEG.")
    return datos


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

        if peticion.firmante.imagen_base64:
            # La imagen que el gerente gestiona en la app (adjunto de su fila en FIRMA_Roles) manda sobre el origen fijo.
            imagen = _decodificar_imagen(peticion.firmante.imagen_base64)
        else:
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
            x_pct=peticion.posicion.x_pct,
            y_pct=peticion.posicion.y_pct,
            lienzo=lienzo_a_tupla(peticion.posicion.lienzo),
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
            firma_mm={
                "x": round(resultado.rect_firma[0] / PT_POR_MM, 1),
                "y": round(resultado.rect_firma[1] / PT_POR_MM, 1),
                "ancho": round((resultado.rect_firma[2] - resultado.rect_firma[0]) / PT_POR_MM, 1),
                "alto": round((resultado.rect_firma[3] - resultado.rect_firma[1]) / PT_POR_MM, 1),
            },
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
