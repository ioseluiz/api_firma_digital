import hashlib

import pymupdf
import pytest

from firma.errores import AnclaNoEncontrada, PaginaFueraDeRango, PdfInvalido
from firma.estampar import PT_POR_MM, ParametrosEstampado, codigo_verificacion, estampar

CODIGO = "FIRMA-2026-000123"


def params(**kw) -> ParametrosEstampado:
    base = dict(codigo=CODIGO, firmante_nombre="Ing. Juan Pérez", firmante_cargo="Gerente de la División de Ingeniería")
    base.update(kw)
    return ParametrosEstampado(**base)


def con_sello(**kw) -> ParametrosEstampado:
    """Las pruebas del sello lo piden explícitamente: por defecto el servicio no escribe texto."""
    return params(sello_texto=True, **kw)


def abrir(b: bytes) -> pymupdf.Document:
    return pymupdf.open(stream=b, filetype="pdf")


def test_firma_en_ultima_pagina_por_defecto(pdf_50_paginas, imagen_firma, fecha):
    r = estampar(pdf_50_paginas, imagen_firma, params(fecha_firma=fecha))
    assert r.paginas == 50
    assert r.pagina_firmada == 50
    assert r.ancla_encontrada is None
    doc = abrir(r.pdf)
    assert doc.page_count == 50
    assert len(doc[49].get_images()) == 1, "la imagen va en la última página"
    assert all(len(doc[i].get_images()) == 0 for i in range(49)), "ninguna otra página se toca"


def test_por_defecto_solo_la_imagen_plana_sin_texto_ni_anotaciones(pdf_1_pagina, imagen_firma, fecha):
    texto_antes = abrir(pdf_1_pagina)[0].get_text()
    r = estampar(pdf_1_pagina, imagen_firma, ParametrosEstampado(codigo=CODIGO, fecha_firma=fecha))
    page = abrir(r.pdf)[0]
    assert page.get_text() == texto_antes, "no se añade ningún texto"
    assert len(page.get_images()) == 1, "la imagen está en el contenido de la página (XObject)"
    assert len(list(page.annots())) == 0, "no es una anotación: no se puede quitar desde un visor"
    assert len(list(page.widgets())) == 0, "tampoco es un campo de formulario"


def test_sello_contiene_nombre_fecha_codigo_y_verificacion(pdf_1_pagina, imagen_firma, fecha):
    r = estampar(pdf_1_pagina, imagen_firma, con_sello(fecha_firma=fecha))
    page = abrir(r.pdf)[0]
    texto = page.get_text()
    assert "Ing. Juan Pérez" in texto
    assert "Gerente de la División de Ingeniería" in texto
    assert "18/11/2026 14:05" in texto
    assert CODIGO in texto
    assert r.codigo_verificacion in texto
    assert len(r.codigo_verificacion) == 12


def test_posicion_fija_en_mm_desde_abajo_izquierda(pdf_1_pagina, imagen_firma, fecha):
    r = estampar(pdf_1_pagina, imagen_firma, params(fecha_firma=fecha, x_mm=30, y_mm=50, ancho_mm=60))
    page = abrir(r.pdf)[0]
    x0, y0, x1, y1 = r.rect_firma
    assert x0 == pytest.approx(30 * PT_POR_MM, abs=0.5)
    assert page.rect.height - y1 == pytest.approx(50 * PT_POR_MM, abs=0.5)
    assert x1 - x0 == pytest.approx(60 * PT_POR_MM, abs=0.5)
    # imagen 3:1 → alto = ancho / 3
    assert y1 - y0 == pytest.approx(20 * PT_POR_MM, abs=0.5)
    rects = page.get_image_rects(page.get_images()[0][0])
    assert rects and rects[0].x0 == pytest.approx(x0, abs=1.0)


def test_pagina_explicita(pdf_50_paginas, imagen_firma, fecha):
    r = estampar(pdf_50_paginas, imagen_firma, params(fecha_firma=fecha, pagina=7))
    assert r.pagina_firmada == 7
    doc = abrir(r.pdf)
    assert len(doc[6].get_images()) == 1
    assert len(doc[49].get_images()) == 0


def test_pagina_fuera_de_rango(pdf_1_pagina, imagen_firma, fecha):
    with pytest.raises(PaginaFueraDeRango):
        estampar(pdf_1_pagina, imagen_firma, params(fecha_firma=fecha, pagina=2))


def test_pdf_cifrado_se_rechaza(pdf_cifrado, imagen_firma, fecha):
    with pytest.raises(PdfInvalido, match="cifrado"):
        estampar(pdf_cifrado, imagen_firma, params(fecha_firma=fecha))


def test_no_pdf_se_rechaza(imagen_firma, fecha):
    with pytest.raises(PdfInvalido):
        estampar(b"hola, no soy un pdf", imagen_firma, params(fecha_firma=fecha))
    with pytest.raises(PdfInvalido):
        estampar(b"%PDF-1.7 pero truncado y roto", imagen_firma, params(fecha_firma=fecha))


def test_ancla_centra_la_firma_sobre_el_texto(pdf_con_ancla, imagen_firma, fecha):
    r = estampar(pdf_con_ancla, imagen_firma, params(fecha_firma=fecha, ancla_texto="Firma del Gerente:"))
    assert r.ancla_encontrada is True
    assert r.pagina_firmada == 3
    page = abrir(r.pdf)[2]
    ancla = page.search_for("Firma del Gerente:")[0]
    x0, y0, x1, y1 = r.rect_firma
    assert (x0 + x1) / 2 == pytest.approx((ancla.x0 + ancla.x1) / 2, abs=0.5), "centrada sobre el ancla"
    assert y1 == pytest.approx(ancla.y0 + 2, abs=0.5), "apoyada sobre el ancla"


def test_ancla_alineada_a_la_izquierda(pdf_con_ancla, imagen_firma, fecha):
    r = estampar(pdf_con_ancla, imagen_firma,
                 params(fecha_firma=fecha, ancla_texto="Firma del Gerente:", ancla_alineacion="izquierda"))
    ancla = abrir(r.pdf)[2].search_for("Firma del Gerente:")[0]
    assert r.rect_firma[0] == pytest.approx(ancla.x0, abs=0.5)


def test_con_ancla_el_sello_no_pisa_el_texto_del_ancla(pdf_con_ancla, imagen_firma, fecha):
    r = estampar(pdf_con_ancla, imagen_firma, con_sello(fecha_firma=fecha, ancla_texto="Firma del Gerente:"))
    page = abrir(r.pdf)[2]
    ancla = page.search_for("Firma del Gerente:")[0]
    sello = page.search_for(CODIGO)[0]
    assert sello.y0 >= ancla.y1 - 0.5, "el sello empieza debajo del texto del ancla"


def _memo_con_bloque_de_firma() -> bytes:
    """Página con el bloque típico: línea, nombre en negrita y cargo debajo."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Memorando de prueba.", fontsize=11, fontname="helv")
    page.insert_text((72, 680), "Atentamente,", fontsize=10, fontname="helv")
    page.insert_text((72, 740), "______________________________", fontsize=10, fontname="helv")
    page.insert_text((72, 755), "Ing. Juan Pérez", fontsize=10, fontname="hebo")
    page.insert_text((72, 768), "Gerente, División de Ingeniería", fontsize=9, fontname="helv")
    return doc.tobytes()


def test_el_sello_no_pisa_el_cargo_debajo_del_nombre(imagen_firma, fecha):
    pdf = _memo_con_bloque_de_firma()
    palabras_antes = [pymupdf.Rect(w[:4]) for w in abrir(pdf)[0].get_text("words")]
    r = estampar(pdf, imagen_firma, con_sello(fecha_firma=fecha, ancla_texto="Ing. Juan Pérez", ancho_mm=45))
    page = abrir(r.pdf)[0]
    sello = page.search_for(CODIGO)[0]
    cargo = page.search_for("Gerente, División de Ingeniería")[0]
    assert sello.y0 >= cargo.y1 - 0.5, "el sello va debajo del cargo, no encima"
    # Y en general: ninguna palabra que ya existía se solapa con la línea del sello.
    assert not [w for w in palabras_antes if w.intersects(sello)]


def test_sello_encima_si_no_hay_sitio_abajo(imagen_firma, fecha):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Documento.", fontsize=11, fontname="helv")
    r = estampar(doc.tobytes(), imagen_firma, con_sello(fecha_firma=fecha, x_mm=30, y_mm=4, ancho_mm=50))
    page = abrir(r.pdf)[0]
    sello = page.search_for(CODIGO)[0]
    assert sello.y1 <= r.rect_firma[1] + 0.5, "sin espacio abajo, el sello va encima de la firma"


def test_sello_sin_nombre_ni_cargo(pdf_1_pagina, imagen_firma, fecha):
    r = estampar(pdf_1_pagina, imagen_firma, ParametrosEstampado(codigo=CODIGO, fecha_firma=fecha, sello_texto=True))
    texto = abrir(r.pdf)[0].get_text()
    assert "Firmado electrónicamente · 18/11/2026 14:05" in texto
    assert " por " not in texto


def test_ancla_ausente_usa_posicion_fija_por_defecto(pdf_1_pagina, imagen_firma, fecha):
    r = estampar(pdf_1_pagina, imagen_firma, params(fecha_firma=fecha, ancla_texto="No existe", x_mm=100, y_mm=30))
    assert r.ancla_encontrada is False
    assert r.rect_firma[0] == pytest.approx(100 * PT_POR_MM, abs=0.5)


def test_ancla_ausente_con_modo_error(pdf_1_pagina, imagen_firma, fecha):
    with pytest.raises(AnclaNoEncontrada):
        estampar(pdf_1_pagina, imagen_firma, params(fecha_firma=fecha, ancla_texto="No existe", si_no_hay_ancla="error"))


def test_apaisado_queda_dentro_de_la_pagina(pdf_apaisado, imagen_firma, fecha):
    r = estampar(pdf_apaisado, imagen_firma, params(fecha_firma=fecha, x_mm=280, y_mm=0, ancho_mm=80))
    page = abrir(r.pdf)[1]
    assert page.rect.width > page.rect.height
    x0, y0, x1, y1 = r.rect_firma
    assert x1 <= page.rect.x1 and y1 <= page.rect.y1 and x0 >= 0 and y0 >= 0, "se desplaza para no salirse"


def test_hash_del_archivo_final_y_codigo_de_verificacion(pdf_1_pagina, imagen_firma, fecha):
    r = estampar(pdf_1_pagina, imagen_firma, params(fecha_firma=fecha))
    assert r.sha256 == hashlib.sha256(r.pdf).hexdigest()
    assert r.codigo_verificacion == codigo_verificacion(pdf_1_pagina, CODIGO, fecha)
    # Determinista respecto al original; cambia si cambia el código o la fecha.
    assert codigo_verificacion(pdf_1_pagina, "OTRO", fecha) != r.codigo_verificacion


def test_sin_sello_aunque_haya_nombre(pdf_1_pagina, imagen_firma, fecha):
    r = estampar(pdf_1_pagina, imagen_firma, params(fecha_firma=fecha))
    page = abrir(r.pdf)[0]
    assert CODIGO not in page.get_text() and "Juan" not in page.get_text()
    assert len(page.get_images()) == 1


def test_sello_sin_codigo_de_verificacion(pdf_1_pagina, imagen_firma, fecha):
    r = estampar(pdf_1_pagina, imagen_firma, con_sello(fecha_firma=fecha, sello_codigo=False))
    texto = abrir(r.pdf)[0].get_text()
    assert "Verif." not in texto
    assert CODIGO in texto


def test_metadatos_llevan_el_codigo(pdf_1_pagina, imagen_firma, fecha):
    r = estampar(pdf_1_pagina, imagen_firma, params(fecha_firma=fecha))
    assert CODIGO in abrir(r.pdf).metadata["keywords"]


def test_imagen_invalida(pdf_1_pagina, fecha):
    with pytest.raises(PdfInvalido, match="imagen"):
        estampar(pdf_1_pagina, b"no soy una imagen", params(fecha_firma=fecha))
