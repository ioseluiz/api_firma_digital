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


def test_sello_contiene_nombre_fecha_codigo_y_verificacion(pdf_1_pagina, imagen_firma, fecha):
    r = estampar(pdf_1_pagina, imagen_firma, params(fecha_firma=fecha))
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


def test_ancla_coloca_la_firma_sobre_el_texto(pdf_con_ancla, imagen_firma, fecha):
    r = estampar(pdf_con_ancla, imagen_firma, params(fecha_firma=fecha, ancla_texto="Firma del Gerente:"))
    assert r.ancla_encontrada is True
    assert r.pagina_firmada == 3
    page = abrir(r.pdf)[2]
    ancla = page.search_for("Firma del Gerente:")[0]
    x0, y0, x1, y1 = r.rect_firma
    assert x0 == pytest.approx(ancla.x0, abs=0.5), "alineada a la izquierda del ancla"
    assert y1 == pytest.approx(ancla.y0 + 2, abs=0.5), "apoyada sobre el ancla"


def test_con_ancla_el_sello_no_pisa_el_texto_del_ancla(pdf_con_ancla, imagen_firma, fecha):
    r = estampar(pdf_con_ancla, imagen_firma, params(fecha_firma=fecha, ancla_texto="Firma del Gerente:"))
    page = abrir(r.pdf)[2]
    ancla = page.search_for("Firma del Gerente:")[0]
    sello = page.search_for(CODIGO)[0]
    assert sello.y0 >= ancla.y1 - 0.5, "el sello empieza debajo del texto del ancla"


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


def test_sin_sello(pdf_1_pagina, imagen_firma, fecha):
    r = estampar(pdf_1_pagina, imagen_firma, params(fecha_firma=fecha, sello_texto=False))
    page = abrir(r.pdf)[0]
    assert CODIGO not in page.get_text()
    assert len(page.get_images()) == 1


def test_sello_sin_codigo_de_verificacion(pdf_1_pagina, imagen_firma, fecha):
    r = estampar(pdf_1_pagina, imagen_firma, params(fecha_firma=fecha, sello_codigo=False))
    texto = abrir(r.pdf)[0].get_text()
    assert "Verif." not in texto
    assert CODIGO in texto


def test_metadatos_llevan_el_codigo(pdf_1_pagina, imagen_firma, fecha):
    r = estampar(pdf_1_pagina, imagen_firma, params(fecha_firma=fecha))
    assert CODIGO in abrir(r.pdf).metadata["keywords"]


def test_imagen_invalida(pdf_1_pagina, fecha):
    with pytest.raises(PdfInvalido, match="imagen"):
        estampar(pdf_1_pagina, b"no soy una imagen", params(fecha_firma=fecha))
