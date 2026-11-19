"""Genera firmas_local/gerente.png y firmas_local/suplente.png: garabatos de PRUEBA, no firmas reales.

Uso:  python scripts/generar_firma_prueba.py [carpeta]
La carpeta firmas_local/ está en .gitignore; las imágenes reales las entrega el gerente a TI y van a Blob.
"""

import sys
from pathlib import Path

import pymupdf


def garabato(ancho: int, alto: int, color, etiqueta: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=ancho, height=alto)
    shape = page.new_shape()
    shape.draw_bezier((40, 150), (120, 20), (260, 220), (340, 60))
    shape.draw_bezier((340, 60), (420, -40), (480, 240), (560, 110))
    shape.finish(color=color, width=7)
    shape.commit()
    page.insert_text(pymupdf.Point(40, alto - 12), etiqueta, fontsize=14, fontname="helv", color=(0.6, 0.1, 0.1))
    return page.get_pixmap(alpha=True).tobytes("png")


def main() -> None:
    carpeta = Path(sys.argv[1] if len(sys.argv) > 1 else "firmas_local")
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "gerente.png").write_bytes(garabato(600, 200, (0.05, 0.13, 0.38), "FIRMA DE PRUEBA - GERENTE"))
    (carpeta / "suplente.png").write_bytes(garabato(600, 200, (0.0, 0.3, 0.1), "FIRMA DE PRUEBA - SUPLENTE"))
    print(f"Generadas gerente.png y suplente.png en {carpeta.resolve()}")


if __name__ == "__main__":
    main()
