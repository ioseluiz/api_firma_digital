"""Lógica pura de estampado: bytes de PDF + bytes de imagen + parámetros → bytes de PDF firmado.

No sabe nada de HTTP, Azure ni SharePoint. Así se prueba sola con pytest.

Convenciones de coordenadas:
  - La petición habla en milímetros desde la esquina INFERIOR izquierda (como lo piensa una persona
    con la hoja en la mano).
  - PyMuPDF trabaja en puntos (1/72 pulgada) desde la esquina SUPERIOR izquierda y, para páginas
    rotadas, `page.rect` ya devuelve el rectángulo tal como se ve. Las funciones de inserción
    (`insert_image`, `insert_textbox`) también reciben coordenadas "como se ve".
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pymupdf

from .errores import AnclaNoEncontrada, PaginaFueraDeRango, PdfInvalido

PT_POR_MM = 72.0 / 25.4

TEXTO_SELLO_POR_DEFECTO = (
    "Firmado electrónicamente por {nombre}{cargo} · {fecha} · {codigo} · Verif. {verif}"
)

# Sello de texto bajo la firma.
_SELLO_FONT = "helv"           # Helvetica base-14: soporta tildes y ñ (WinAnsi)
_SELLO_TAMANO = 6.5
_SELLO_ANCHO_MIN_PT = 95 * PT_POR_MM
_SELLO_ALTO_PT = 2 * (_SELLO_TAMANO * 1.25) + 2   # dos líneas
_SELLO_COLOR = (0.35, 0.35, 0.35)
_MARGEN_PT = 5 * PT_POR_MM


@dataclass(frozen=True)
class ParametrosEstampado:
    codigo: str
    fecha_firma: datetime
    firmante_nombre: str = ""
    firmante_cargo: str = ""
    pagina: int = 0                      # 0 = última
    x_mm: float = 120.0
    y_mm: float = 40.0
    ancho_mm: float = 50.0
    ancla_texto: str = ""
    ancla_alineacion: str = "centro"         # "centro": centrada sobre el ancla; "izquierda": alineada a su borde izquierdo
    si_no_hay_ancla: str = "posicion_fija"   # o "error"
    sello_texto: bool = False                # por defecto solo la imagen, sin texto
    sello_codigo: bool = True
    plantilla_sello: str = TEXTO_SELLO_POR_DEFECTO


@dataclass(frozen=True)
class ResultadoEstampado:
    pdf: bytes
    sha256: str
    codigo_verificacion: str
    paginas: int
    pagina_firmada: int                  # base 1
    ancla_encontrada: Optional[bool]     # None si no se pidió ancla
    rect_firma: tuple[float, float, float, float]   # en puntos, "como se ve"; para pruebas y diagnóstico


def codigo_verificacion(pdf_original: bytes, codigo: str, fecha_firma: datetime) -> str:
    """12 hex en mayúsculas, deterministas para (original, código, fecha).

    Se estampa en el sello. Quien tenga el original en SharePoint puede recomputarlo y comparar.
    El hash del archivo FINAL (que no puede estamparse dentro de sí mismo) se devuelve aparte
    como `sha256` y se guarda en la fila de la solicitud.
    """
    h = hashlib.sha256()
    h.update(pdf_original)
    h.update(b"|")
    h.update(codigo.encode("utf-8"))
    h.update(b"|")
    h.update(fecha_firma.isoformat(timespec="seconds").encode("utf-8"))
    return h.hexdigest()[:12].upper()


def _abrir(pdf: bytes) -> pymupdf.Document:
    if not pdf.lstrip().startswith(b"%PDF"):
        raise PdfInvalido("El archivo no es un PDF (no empieza por %PDF).")
    try:
        doc = pymupdf.open(stream=pdf, filetype="pdf")
    except Exception as e:  # pymupdf lanza distintos tipos según el daño
        raise PdfInvalido(f"No se pudo abrir el PDF: {e}") from e
    if not doc.is_pdf:
        doc.close()
        raise PdfInvalido("El archivo no es un PDF.")
    if doc.is_encrypted or doc.needs_pass:
        doc.close()
        raise PdfInvalido("El PDF está cifrado o protegido con contraseña; quítale la protección antes de enviarlo.")
    if doc.page_count < 1:
        doc.close()
        raise PdfInvalido("El PDF no tiene páginas.")
    if _tiene_xfa(doc):
        doc.close()
        raise PdfInvalido("El PDF es un formulario XFA; expórtalo a PDF plano (Imprimir a PDF) antes de enviarlo.")
    return doc


def _tiene_xfa(doc: pymupdf.Document) -> bool:
    try:
        catalogo = doc.pdf_catalog()
        tipo, acro = doc.xref_get_key(catalogo, "AcroForm")
        if tipo == "xref":
            xref = int(acro.split()[0])
            tipo_xfa, _ = doc.xref_get_key(xref, "XFA")
            return tipo_xfa not in ("null", None)
        if tipo == "dict":
            return "/XFA" in acro
    except Exception:
        return False
    return False


def _indice_pagina(doc: pymupdf.Document, pagina: int) -> int:
    n = doc.page_count
    if pagina == 0:
        return n - 1
    if pagina > n:
        raise PaginaFueraDeRango(f"Se pidió firmar la página {pagina} y el documento tiene {n}.")
    return pagina - 1


def _tamano_imagen(imagen: bytes) -> tuple[float, float]:
    try:
        pix = pymupdf.Pixmap(imagen)
    except Exception as e:
        raise PdfInvalido(f"La imagen de la firma no se pudo leer: {e}") from e
    if pix.width <= 0 or pix.height <= 0:
        raise PdfInvalido("La imagen de la firma está vacía.")
    return float(pix.width), float(pix.height)


def _ajustar_dentro(rect: pymupdf.Rect, pagina: pymupdf.Rect, margen_pt: float = _MARGEN_PT) -> pymupdf.Rect:
    """Desplaza el rectángulo para que quede dentro de la página con un margen. No lo encoge."""
    r = pymupdf.Rect(rect)
    if r.x1 > pagina.x1 - margen_pt:
        d = r.x1 - (pagina.x1 - margen_pt)
        r.x0 -= d
        r.x1 -= d
    if r.x0 < pagina.x0 + margen_pt:
        d = (pagina.x0 + margen_pt) - r.x0
        r.x0 += d
        r.x1 += d
    if r.y1 > pagina.y1 - margen_pt:
        d = r.y1 - (pagina.y1 - margen_pt)
        r.y0 -= d
        r.y1 -= d
    if r.y0 < pagina.y0 + margen_pt:
        d = (pagina.y0 + margen_pt) - r.y0
        r.y0 += d
        r.y1 += d
    return r


def _rect_firma(
    page: pymupdf.Page, p: ParametrosEstampado, ancho_pt: float, alto_pt: float
) -> tuple[pymupdf.Rect, Optional[bool], Optional[pymupdf.Rect]]:
    """Devuelve (rectángulo de la imagen, si se encontró el ancla [None si no se pidió], rect del ancla)."""
    rect_pagina = page.rect
    ancla_encontrada: Optional[bool] = None

    if p.ancla_texto.strip():
        hits = page.search_for(p.ancla_texto.strip())
        if hits:
            ancla_encontrada = True
            # La última aparición: las firmas suelen ir al final de la página.
            a = hits[-1]
            # Imagen apoyada sobre el ancla, solapando 2 pt para que una firma sobre una línea
            # "________" se vea natural. Centrada sobre el rótulo (bloque típico: línea y debajo
            # el nombre o el cargo centrados) o alineada a su izquierda ("Firma: ______").
            if p.ancla_alineacion == "izquierda":
                x0 = a.x0
            else:
                x0 = (a.x0 + a.x1) / 2 - ancho_pt / 2
            rect = pymupdf.Rect(x0, a.y0 - alto_pt + 2, x0 + ancho_pt, a.y0 + 2)
            return _ajustar_dentro(rect, rect_pagina), ancla_encontrada, a
        ancla_encontrada = False
        if p.si_no_hay_ancla == "error":
            raise AnclaNoEncontrada(f"No se encontró el texto {p.ancla_texto!r} en la página {page.number + 1}.")

    x0 = rect_pagina.x0 + p.x_mm * PT_POR_MM
    y1 = rect_pagina.y1 - p.y_mm * PT_POR_MM          # base de la imagen, medida desde abajo
    rect = pymupdf.Rect(x0, y1 - alto_pt, x0 + ancho_pt, y1)
    return _ajustar_dentro(rect, rect_pagina), ancla_encontrada, None


def _texto_sello(p: ParametrosEstampado, verif: str) -> str:
    nombre = p.firmante_nombre.strip()
    cargo = p.firmante_cargo.strip()
    cargo = f", {cargo}" if (cargo and nombre) else cargo
    texto = p.plantilla_sello.format(
        nombre=nombre,
        cargo=cargo,
        fecha=p.fecha_firma.strftime("%d/%m/%Y %H:%M"),
        codigo=p.codigo,
        verif=verif,
    )
    if not nombre and not cargo:
        texto = texto.replace(" por  ·", " ·").replace(" por ·", " ·")
    if not p.sello_codigo:
        texto = texto.replace(f" · Verif. {verif}", "").replace(f"Verif. {verif}", "").rstrip(" ·")
    return texto


def _caja_sello(
    page: pymupdf.Page, rect_firma: pymupdf.Rect, rect_ancla: Optional[pymupdf.Rect]
) -> pymupdf.Rect:
    """Elige dónde va el sello sin pisar texto existente.

    Orden: debajo de la firma (y del ancla), bajando por encima de cada línea de texto que estorbe;
    si no hay sitio abajo, encima de la firma; si tampoco, a la derecha; si nada está libre, debajo
    de todos modos (mejor un sello solapado que ningún sello).
    """
    rect_pagina = page.rect
    limite_inferior = rect_pagina.y1 - 3 * PT_POR_MM
    ancho = max(rect_firma.width, _SELLO_ANCHO_MIN_PT)
    alto = _SELLO_ALTO_PT
    x0 = rect_firma.x0
    if x0 + ancho > rect_pagina.x1 - _MARGEN_PT:
        x0 = max(rect_pagina.x0 + _MARGEN_PT, rect_pagina.x1 - _MARGEN_PT - ancho)

    palabras = [pymupdf.Rect(w[:4]) for w in page.get_text("words")]

    def estorban(caja: pymupdf.Rect) -> list[pymupdf.Rect]:
        return [r for r in palabras if not r.is_empty and r.intersects(caja)]

    tope = rect_firma.y1 if rect_ancla is None else max(rect_firma.y1, rect_ancla.y1)
    primera = pymupdf.Rect(x0, tope + 1, x0 + ancho, tope + 1 + alto)

    # 1) Debajo, bajando línea a línea mientras haya texto en medio.
    caja = pymupdf.Rect(primera)
    for _ in range(8):
        if caja.y1 > limite_inferior:
            break
        ocupado = estorban(caja)
        if not ocupado:
            return caja
        y = max(r.y1 for r in ocupado) + 1
        caja = pymupdf.Rect(x0, y, x0 + ancho, y + alto)

    # 2) Encima de la firma.
    caja = pymupdf.Rect(x0, rect_firma.y0 - 1 - alto, x0 + ancho, rect_firma.y0 - 1)
    if caja.y0 >= rect_pagina.y0 + _MARGEN_PT and not estorban(caja):
        return caja

    # 3) A la derecha de la firma, alineado con su base.
    caja = pymupdf.Rect(rect_firma.x1 + 3, rect_firma.y1 - alto, rect_firma.x1 + 3 + ancho, rect_firma.y1)
    if caja.x1 <= rect_pagina.x1 - _MARGEN_PT and not estorban(caja):
        return caja

    return primera if primera.y1 <= limite_inferior else pymupdf.Rect(
        x0, rect_firma.y0 - 1 - alto, x0 + ancho, rect_firma.y0 - 1
    )


def _insertar_sello(
    page: pymupdf.Page, rect_firma: pymupdf.Rect, texto: str, rect_ancla: Optional[pymupdf.Rect] = None
) -> None:
    caja = _caja_sello(page, rect_firma, rect_ancla)
    sobrante = page.insert_textbox(
        caja, texto, fontsize=_SELLO_TAMANO, fontname=_SELLO_FONT, color=_SELLO_COLOR,
        align=pymupdf.TEXT_ALIGN_LEFT,
    )
    if sobrante < 0:
        # No cupo en dos líneas: una sola línea sin recorte, aunque sea larga.
        page.insert_text(
            pymupdf.Point(caja.x0, caja.y0 + _SELLO_TAMANO + 1), texto,
            fontsize=_SELLO_TAMANO - 0.5, fontname=_SELLO_FONT, color=_SELLO_COLOR,
        )


def estampar(pdf: bytes, imagen: bytes, p: ParametrosEstampado) -> ResultadoEstampado:
    """Estampa la firma y el sello. Función pura: no toca disco ni red."""
    verif = codigo_verificacion(pdf, p.codigo, p.fecha_firma)
    img_w, img_h = _tamano_imagen(imagen)
    ancho_pt = p.ancho_mm * PT_POR_MM
    alto_pt = ancho_pt * img_h / img_w

    doc = _abrir(pdf)
    try:
        idx = _indice_pagina(doc, p.pagina)
        page = doc[idx]
        rect, ancla_encontrada, rect_ancla = _rect_firma(page, p, ancho_pt, alto_pt)

        # insert_image dibuja la imagen en el flujo de contenido de la página (como un XObject),
        # no como anotación: queda "plana", igual que si viniera impresa en el documento.
        page.insert_image(rect, stream=imagen, keep_proportion=True, overlay=True)
        if p.sello_texto:
            _insertar_sello(page, rect, _texto_sello(p, verif), rect_ancla)

        md = dict(doc.metadata or {})
        md["keywords"] = f"{p.codigo}; firmado electrónicamente"
        doc.set_metadata(md)

        salida = doc.tobytes(deflate=True)
        return ResultadoEstampado(
            pdf=salida,
            sha256=hashlib.sha256(salida).hexdigest(),
            codigo_verificacion=verif,
            paginas=doc.page_count,
            pagina_firmada=idx + 1,
            ancla_encontrada=ancla_encontrada,
            rect_firma=(rect.x0, rect.y0, rect.x1, rect.y1),
        )
    finally:
        doc.close()
