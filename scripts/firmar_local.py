"""Cliente de línea de comandos: manda un PDF al API y guarda el firmado.

Sirve para probar el servicio sin Power Automate y para que quien arme el flujo vea el JSON exacto.

Ejemplos:
  python scripts/firmar_local.py --pdf memo.pdf
  python scripts/firmar_local.py --pdf memo.pdf --ancla "Firma del Gerente:" --pagina 0
  python scripts/firmar_local.py --pdf memo.pdf --url https://func-ini-firma-dev.azurewebsites.net/api/v1/firmar --clave <function key>
  python scripts/firmar_local.py --pdf memo.pdf --solo-json > peticion.json
"""

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


def construir_peticion(a: argparse.Namespace) -> dict:
    pdf = Path(a.pdf).read_bytes()
    return {
        "solicitud_id": a.solicitud_id,
        "codigo": a.codigo,
        "pdf_base64": base64.b64encode(pdf).decode("ascii"),
        "firmante": {"nombre": a.nombre, "cargo": a.cargo, "email": a.email, "imagen_id": a.imagen_id},
        "fecha_firma": a.fecha or datetime.now().replace(microsecond=0).isoformat(),
        "posicion": {
            "pagina": a.pagina, "x_mm": a.x_mm, "y_mm": a.y_mm, "ancho_mm": a.ancho_mm,
            "ancla_texto": a.ancla, "si_no_hay_ancla": "error" if a.ancla_obligatoria else "posicion_fija",
        },
        "sello": {"texto": not a.sin_sello, "codigo_verificacion": not a.sin_verificacion},
    }


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):  # consolas de Windows con cp1252: que las tildes salgan bien
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pdf", required=True)
    p.add_argument("--salida", help="ruta del PDF firmado (por defecto <pdf>_firmado.pdf)")
    p.add_argument("--url", default="http://localhost:7071/api/v1/firmar")
    p.add_argument("--clave", default="", help="clave de función (x-functions-key) si el API la exige")
    p.add_argument("--token", default="", help="token Bearer de Entra ID si el API está detrás de Easy Auth")
    p.add_argument("--solicitud-id", type=int, default=1)
    p.add_argument("--codigo", default="FIRMA-2026-000001")
    p.add_argument("--nombre", default="Ing. Juan Pérez")
    p.add_argument("--cargo", default="Gerente de la División")
    p.add_argument("--email", default="")
    p.add_argument("--imagen-id", default="gerente.png")
    p.add_argument("--fecha", default="", help="ISO 8601 local; por defecto ahora")
    p.add_argument("--pagina", type=int, default=0, help="0 = última")
    p.add_argument("--x-mm", type=float, default=120)
    p.add_argument("--y-mm", type=float, default=40)
    p.add_argument("--ancho-mm", type=float, default=50)
    p.add_argument("--ancla", default="")
    p.add_argument("--ancla-obligatoria", action="store_true")
    p.add_argument("--sin-sello", action="store_true")
    p.add_argument("--sin-verificacion", action="store_true")
    p.add_argument("--solo-json", action="store_true", help="imprime la petición y no llama al API")
    a = p.parse_args()

    cuerpo = construir_peticion(a)
    if a.solo_json:
        print(json.dumps(cuerpo, ensure_ascii=False, indent=2))
        return 0

    headers = {"Content-Type": "application/json", "X-Correlation-Id": f"cli-{datetime.now():%Y%m%d%H%M%S}"}
    if a.clave:
        headers["x-functions-key"] = a.clave
    if a.token:
        headers["Authorization"] = f"Bearer {a.token}"
    req = urllib.request.Request(a.url, data=json.dumps(cuerpo).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            estado, texto = r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        estado, texto = e.code, e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        print(f"No se pudo conectar con {a.url}: {e.reason}", file=sys.stderr)
        return 2

    try:
        datos = json.loads(texto)
    except json.JSONDecodeError:
        print(f"HTTP {estado}, respuesta no JSON:\n{texto[:500]}", file=sys.stderr)
        return 3

    if estado != 200 or not datos.get("ok"):
        print(f"HTTP {estado}: {datos.get('error')} — {datos.get('mensaje')} (corr {datos.get('correlation_id')})", file=sys.stderr)
        return 1

    salida = Path(a.salida) if a.salida else Path(a.pdf).with_name(Path(a.pdf).stem + "_firmado.pdf")
    salida.write_bytes(base64.b64decode(datos["pdf_firmado_base64"]))
    resumen = {k: v for k, v in datos.items() if k != "pdf_firmado_base64"}
    print(json.dumps(resumen, ensure_ascii=False, indent=2))
    print(f"PDF firmado guardado en {salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
