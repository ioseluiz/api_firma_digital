"""Fixtures: PDFs e imagen de firma generados en memoria con PyMuPDF. No hay archivos binarios en el repo."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pymupdf
import pytest

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

A4 = pymupdf.paper_rect("a4")
FECHA = datetime(2026, 11, 18, 14, 5, 12)


def _pdf(paginas: int = 1, *, apaisado: bool = False, texto_final: str = "", clave: str = "") -> bytes:
    doc = pymupdf.open()
    ancho, alto = (A4.height, A4.width) if apaisado else (A4.width, A4.height)
    for i in range(paginas):
        page = doc.new_page(width=ancho, height=alto)
        page.insert_text(pymupdf.Point(72, 72), f"Memorando de prueba — página {i + 1} de {paginas}", fontsize=12, fontname="helv")
        page.insert_text(pymupdf.Point(72, 100), "Contenido del documento.", fontsize=11, fontname="helv")
        if texto_final and i == paginas - 1:
            page.insert_text(pymupdf.Point(72, alto - 120), texto_final, fontsize=11, fontname="helv")
    if clave:
        return doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw=clave, user_pw=clave)
    return doc.tobytes()


def _png_firma(ancho: int = 600, alto: int = 200) -> bytes:
    """Garabato azul sobre fondo transparente, proporción 3:1."""
    doc = pymupdf.open()
    page = doc.new_page(width=ancho, height=alto)
    shape = page.new_shape()
    shape.draw_bezier((40, 150), (120, 20), (260, 220), (340, 60))
    shape.draw_bezier((340, 60), (420, -40), (480, 240), (560, 110))
    shape.finish(color=(0.05, 0.13, 0.38), width=7)
    shape.commit()
    pix = page.get_pixmap(alpha=True)
    return pix.tobytes("png")


@pytest.fixture(scope="session")
def imagen_firma() -> bytes:
    return _png_firma()


@pytest.fixture
def pdf_1_pagina() -> bytes:
    return _pdf(1)


@pytest.fixture
def pdf_50_paginas() -> bytes:
    return _pdf(50)


@pytest.fixture
def pdf_apaisado() -> bytes:
    return _pdf(2, apaisado=True)


@pytest.fixture
def pdf_con_ancla() -> bytes:
    return _pdf(3, texto_final="Firma del Gerente: ____________________")


@pytest.fixture
def pdf_cifrado() -> bytes:
    return _pdf(1, clave="secreto")


@pytest.fixture
def fecha() -> datetime:
    return FECHA


@pytest.fixture
def carpeta_firmas(tmp_path: Path, imagen_firma: bytes) -> Path:
    (tmp_path / "gerente.png").write_bytes(imagen_firma)
    return tmp_path


@pytest.fixture
def app_local(carpeta_firmas: Path, monkeypatch: pytest.MonkeyPatch):
    """function_app configurado con origen local sobre una carpeta temporal."""
    monkeypatch.setenv("FIRMAS_ORIGEN", "local")
    monkeypatch.setenv("FIRMAS_CARPETA", str(carpeta_firmas))
    monkeypatch.setenv("FIRMAS_IMAGEN_POR_DEFECTO", "gerente.png")
    monkeypatch.setenv("FIRMA_MAX_PDF_MB", "25")
    monkeypatch.delenv("FIRMA_TEXTO_SELLO", raising=False)
    import function_app

    function_app.reiniciar_origen()
    yield function_app
    function_app.reiniciar_origen()
