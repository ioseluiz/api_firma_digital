"""Sube la definición de un flujo (archivo JSON del repo) al flujo del mismo nombre en Dataverse.

Hace, en este orden, y se detiene ante cualquier duda:
  1. busca el flujo por nombre (category 5 = flujo de nube) y exige exactamente uno;
  2. guarda un respaldo del clientdata vivo en flujos/respaldo/ y muestra el diff normalizado contra
     el archivo del repo: si alguien tocó el flujo en el portal, se ve aquí (skill §2.2);
  3. comprueba que las referencias de conexión del repo existen en el flujo vivo y que toda variable
     de entorno referenciada tiene fila de valor (skill §2.5b);
  4. valida los parámetros contra el swagger (validar_parametros.py), salvo --sin-validar;
  5. apaga el flujo, PATCH-ea clientdata y, con --activar, lo enciende (apagar/encender reengancha la
     marca de agua del sondeo, skill §2.3).

Uso:
  python flujos/subir_flujo.py --flujo flujos/FIRMA_F02_Aprobar_y_firmar.json --si --activar
  python flujos/subir_flujo.py --exportar            # baja el clientdata vivo de todos los FIRMA F0% a flujos/
  python flujos/subir_flujo.py --crear flujos/FIRMA_F02_Aprobar_y_firmar.json --solucion FirmadeDocumentos --si
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dataverse import Dataverse  # noqa: E402

AQUI = Path(__file__).resolve().parent
RESPALDO = AQUI / "respaldo"
PARAM_RE = re.compile(r"parameters\('([^']+) \(([^)]+)\)'\)")


def normalizado(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1)


def nombre_de(archivo: Path) -> str:
    """El nombre del flujo está en el archivo (clave 'nombre' en la raíz) o se deriva del nombre del archivo."""
    d = json.loads(archivo.read_text(encoding="utf-8"))
    if d.get("nombre"):
        return d["nombre"]
    base = archivo.stem  # FIRMA_F02_Aprobar_y_firmar -> FIRMA F02 · Aprobar y firmar
    m = re.match(r"(FIRMA)_(F\d+)_(.+)", base)
    if not m:
        raise SystemExit(f"No puedo derivar el nombre del flujo de {archivo.name}; agrega 'nombre' en la raíz del JSON")
    return f"{m.group(1)} {m.group(2)} · {m.group(3).replace('_', ' ')}"


def definicion_de(archivo: Path) -> dict:
    d = json.loads(archivo.read_text(encoding="utf-8"))
    d.pop("nombre", None)
    return d


def buscar(dv: Dataverse, nombre: str) -> dict:
    r = dv.get("workflows", params={
        "$select": "workflowid,name,statecode,statuscode,clientdata,_ownerid_value,modifiedon",
        "$filter": f"category eq 5 and name eq '{nombre.replace(chr(39), chr(39) * 2)}'",
    })
    filas = r.get("value", [])
    if len(filas) != 1:
        raise SystemExit(f"Se esperaba exactamente un flujo llamado {nombre!r}; hay {len(filas)}.")
    return filas[0]


def comprobar_variables(dv: Dataverse, definicion: dict) -> list[str]:
    problemas = []
    nombres = {m.group(2) for m in PARAM_RE.finditer(json.dumps(definicion, ensure_ascii=False))}
    for schema in sorted(nombres):
        r = dv.get("environmentvariabledefinitions", params={
            "$select": "schemaname,defaultvalue",
            "$filter": f"schemaname eq '{schema}'",
            "$expand": "environmentvariabledefinition_environmentvariablevalue($select=value)",
        })
        filas = r.get("value", [])
        if not filas:
            problemas.append(f"variable {schema} no existe en el ambiente")
            continue
        valores = [v.get("value") for v in filas[0].get("environmentvariabledefinition_environmentvariablevalue", [])]
        if not any(v for v in valores):
            problemas.append(f"variable {schema} sin fila de valor (el flujo no se podrá encender); usa variables_entorno.py")
    return problemas


def comprobar_referencias(vivo: dict, definicion: dict) -> list[str]:
    try:
        vivas = {v.get("connection", {}).get("connectionReferenceLogicalName")
                 for v in json.loads(vivo["clientdata"])["properties"].get("connectionReferences", {}).values()}
    except Exception:
        vivas = set()
    nuevas = {v.get("connection", {}).get("connectionReferenceLogicalName")
              for v in definicion["properties"].get("connectionReferences", {}).values()}
    faltan = nuevas - vivas
    return [f"referencia de conexión {x!r} no está en el flujo vivo: créala/elígela en el portal antes" for x in sorted(faltan)] if faltan else []


def validar(archivo: Path) -> None:
    r = subprocess.run([sys.executable, str(AQUI / "validar_parametros.py"), str(archivo)], text=True)
    if r.returncode != 0:
        raise SystemExit("validar_parametros.py encontró problemas; corrige antes de subir (o --sin-validar bajo tu responsabilidad).")


def exportar(dv: Dataverse) -> None:
    r = dv.get("workflows", params={"$select": "name,clientdata,statecode", "$filter": "category eq 5 and startswith(name,'FIRMA F0')"})
    for w in r.get("value", []):
        nombre = re.sub(r"[^A-Za-z0-9]+", "_", w["name"]).strip("_")
        destino = AQUI / f"{nombre}.exportado.json"
        d = json.loads(w["clientdata"])
        d["nombre"] = w["name"]
        destino.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  {w['name']} ({'Activated' if w['statecode'] == 1 else 'Draft'}) -> {destino.name}")


def crear(dv: Dataverse, archivo: Path, solucion: str, si: bool) -> None:
    """Crea el flujo directamente por Web API (sin esqueleto en el diseñador) y lo mete en la solución."""
    nombre = nombre_de(archivo)
    definicion = definicion_de(archivo)
    existentes = dv.get("workflows", params={"$select": "workflowid", "$filter": f"category eq 5 and name eq '{nombre}'"}).get("value", [])
    if existentes:
        raise SystemExit(f"Ya existe un flujo llamado {nombre!r}: usa --flujo para actualizarlo.")
    problemas = comprobar_variables(dv, definicion)
    for p in problemas:
        print("  AVISO:", p)
    if not si:
        print(f"Se crearía el flujo {nombre!r} en la solución {solucion}. Repite con --si para hacerlo.")
        return
    cuerpo = {
        "category": 5,
        "name": nombre,
        "description": f"Plan_Firma_INI · {nombre}",
        "type": 1,
        "primaryentity": "none",
        "clientdata": json.dumps(definicion, ensure_ascii=False, separators=(",", ":")),
        "statecode": 0,
        "statuscode": 1,
    }
    r = dv.post("workflows", cuerpo, extra={"MSCRM.SolutionUniqueName": solucion, "Prefer": "return=representation"})
    print(f"  creado {r.get('name')} id={r.get('workflowid')} (Draft, en la solución {solucion})")


def subir(dv: Dataverse, archivo: Path, si: bool, activar: bool, sin_validar: bool) -> None:
    nombre = nombre_de(archivo)
    definicion = definicion_de(archivo)
    vivo = buscar(dv, nombre)
    RESPALDO.mkdir(exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d-%H%M%S")
    respaldo = RESPALDO / f"{archivo.stem}_{marca}.json"
    respaldo.write_text(vivo["clientdata"], encoding="utf-8")
    print(f"Flujo: {nombre} ({vivo['workflowid']}), estado {'Activated' if vivo['statecode'] == 1 else 'Draft'}, modificado {vivo['modifiedon']}")
    print(f"Respaldo del clientdata vivo: {respaldo.relative_to(AQUI.parent)}")

    vivo_norm = normalizado(json.loads(vivo["clientdata"])).splitlines()
    nuevo_norm = normalizado(definicion).splitlines()
    diff = list(difflib.unified_diff(vivo_norm, nuevo_norm, "vivo", "repo", lineterm="", n=2))
    if not diff:
        print("El flujo vivo ya es idéntico al repo.")
    else:
        print(f"Diff vivo -> repo ({len(diff)} líneas):")
        for linea in diff[:400]:
            print("  " + linea)
        if len(diff) > 400:
            print(f"  … ({len(diff) - 400} líneas más)")

    problemas = comprobar_referencias(vivo, definicion) + comprobar_variables(dv, definicion)
    for p in problemas:
        print("  PROBLEMA:", p)
    if problemas:
        raise SystemExit("Corrige los problemas antes de subir.")
    if not sin_validar:
        validar(archivo)
    if not si:
        print("Nada se ha escrito. Repite con --si para aplicar (y --activar para encenderlo).")
        return

    wid = vivo["workflowid"]
    estaba_encendido = vivo["statecode"] == 1
    # Primero la definición: si Dataverse la rechaza (licencia, parámetros), el estado del flujo no cambia.
    dv.patch(f"workflows({wid})", {"clientdata": json.dumps(definicion, ensure_ascii=False, separators=(",", ":"))})
    print("clientdata actualizado.")
    if activar or estaba_encendido:
        # Apagar y encender reengancha la marca de agua del sondeo (skill §2.3).
        dv.patch(f"workflows({wid})", {"statecode": 0, "statuscode": 1})
        dv.patch(f"workflows({wid})", {"statecode": 1, "statuscode": 2})
        w = dv.get(f"workflows({wid})", params={"$select": "statecode,statuscode"})
        print(f"encendido: statecode={w.get('statecode')} statuscode={w.get('statuscode')}")
    else:
        print("Queda apagado (estaba apagado): enciéndelo con --activar o con --encender.")


def encender(dv: Dataverse, nombre: str, apagar: bool = False) -> None:
    vivo = buscar(dv, nombre)
    wid = vivo["workflowid"]
    if apagar:
        dv.patch(f"workflows({wid})", {"statecode": 0, "statuscode": 1})
    else:
        dv.patch(f"workflows({wid})", {"statecode": 1, "statuscode": 2})
    w = dv.get(f"workflows({wid})", params={"$select": "statecode,statuscode"})
    print(f"{nombre}: statecode={w.get('statecode')} statuscode={w.get('statuscode')}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--flujo", help="archivo JSON del repo a subir")
    ap.add_argument("--crear", help="archivo JSON del repo: crea el flujo (nuevo) por Web API")
    ap.add_argument("--solucion", default="FirmadeDocumentos")
    ap.add_argument("--exportar", action="store_true", help="baja el clientdata vivo de todos los FIRMA F0%")
    ap.add_argument("--encender", metavar="NOMBRE", help="enciende el flujo con ese nombre (p. ej. 'FIRMA F01 · Registrar envío')")
    ap.add_argument("--apagar", metavar="NOMBRE", help="apaga el flujo con ese nombre")
    ap.add_argument("--si", action="store_true", help="aplica los cambios (sin esto solo muestra)")
    ap.add_argument("--activar", action="store_true")
    ap.add_argument("--sin-validar", action="store_true")
    a = ap.parse_args()
    dv = Dataverse()
    if a.exportar:
        exportar(dv)
    elif a.encender:
        encender(dv, a.encender)
    elif a.apagar:
        encender(dv, a.apagar, apagar=True)
    elif a.crear:
        crear(dv, Path(a.crear), a.solucion, a.si)
    elif a.flujo:
        subir(dv, Path(a.flujo), a.si, a.activar, a.sin_validar)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
