"""Genera el archivo de entorno de Postman con un PDF ya codificado en base64.

Uso:
  python postman/generar_entorno.py --pdf salida/CronogramaPedroMiguelAF27.pdf --imagen-id firma-jose-munoz.png
  python postman/generar_entorno.py --pdf memo.pdf --base-url https://func-ini-firma-dev.azurewebsites.net --clave <function key>

Produce postman/INI_Firma.<nombre>.postman_environment.json; importar en Postman junto con la colección.
El entorno queda ignorado por git (contiene el documento).
"""

import argparse
import base64
import json
import uuid
from pathlib import Path

AQUI = Path(__file__).resolve().parent


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pdf", required=True, help="PDF que se usará en las peticiones de la colección")
    p.add_argument("--imagen-id", default="gerente.png")
    p.add_argument("--base-url", default="http://127.0.0.1:7071")
    p.add_argument("--clave", default="", help="clave de función (x-functions-key) para Azure; vacío en local")
    p.add_argument("--ancla", default="Gerente INIG")
    p.add_argument("--nombre", default="local", help="sufijo del archivo de entorno")
    a = p.parse_args()

    pdf = Path(a.pdf).read_bytes()
    if not pdf.lstrip().startswith(b"%PDF"):
        raise SystemExit(f"{a.pdf} no parece un PDF")

    def var(k: str, v: str, tipo: str = "default") -> dict:
        return {"key": k, "value": v, "type": tipo, "enabled": True}

    entorno = {
        "id": str(uuid.uuid4()),
        "name": f"INI Firma ({a.nombre})",
        "values": [
            var("base_url", a.base_url),
            var("function_key", a.clave, "secret"),
            var("imagen_id", a.imagen_id),
            var("codigo", "FIRMA-2026-000001"),
            var("ancla_texto", a.ancla),
            var("pdf_nombre", Path(a.pdf).name),
            var("pdf_base64", base64.b64encode(pdf).decode("ascii")),
            var("sha256", ""),
            var("codigo_verificacion", ""),
        ],
        "_postman_variable_scope": "environment",
    }
    salida = AQUI / f"INI_Firma.{a.nombre}.postman_environment.json"
    salida.write_text(json.dumps(entorno, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Entorno escrito en {salida}  (PDF: {Path(a.pdf).name}, {len(pdf) / 1e6:.2f} MB; imagen: {a.imagen_id})")


if __name__ == "__main__":
    main()
