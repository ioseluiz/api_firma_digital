"""Variables de entorno de la solución Firma de documentos: listar, crear la definición y fijar el valor.

Una variable sin fila de valor impide encender los flujos que la nombran (skill §2.5b), y una cadena
vacía no crea fila. Por eso `--valor` exige texto y el centinela es SIN_CONFIGURAR.

Uso:
  python flujos/variables_entorno.py --listar
  python flujos/variables_entorno.py --definir ini_FIRMA_ApiKey "FIRMA · Clave del API" --solucion FirmadeDocumentos
  python flujos/variables_entorno.py --fijar ini_FIRMA_ApiUrl "https://func-ini-firma-dev.azurewebsites.net/api/v1"
  python flujos/variables_entorno.py --fijar ini_FIRMA_ApiKey --desde-az   # lee la clave 'powerautomate' con az
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dataverse import AZ, Dataverse  # noqa: E402

PREFIJO = "ini_FIRMA"
TIPO_STRING = 100000000


def listar(dv: Dataverse) -> list[dict]:
    r = dv.get("environmentvariabledefinitions", params={
        "$select": "schemaname,displayname,defaultvalue,environmentvariabledefinitionid",
        "$filter": f"startswith(schemaname,'{PREFIJO}')",
        "$expand": "environmentvariabledefinition_environmentvariablevalue($select=value,environmentvariablevalueid)",
        "$orderby": "schemaname",
    })
    return r.get("value", [])


def definir(dv: Dataverse, schema: str, display: str, solucion: str) -> None:
    existentes = dv.get("environmentvariabledefinitions", params={"$select": "schemaname", "$filter": f"schemaname eq '{schema}'"}).get("value", [])
    if existentes:
        print(f"  {schema} ya existe")
        return
    r = dv.post("environmentvariabledefinitions", {
        "schemaname": schema, "displayname": display, "type": TIPO_STRING,
        "defaultvalue": "SIN_CONFIGURAR", "description": "Plan_Firma_INI",
    }, extra={"MSCRM.SolutionUniqueName": solucion, "Prefer": "return=representation"})
    print(f"  definida {schema} ({r.get('environmentvariabledefinitionid')}) en {solucion}")


def fijar(dv: Dataverse, schema: str, valor: str, solucion: str) -> None:
    if not valor.strip():
        raise SystemExit("El valor no puede estar vacío: Dataverse no guarda filas vacías. Usa SIN_CONFIGURAR.")
    defs = dv.get("environmentvariabledefinitions", params={
        "$select": "environmentvariabledefinitionid",
        "$filter": f"schemaname eq '{schema}'",
        "$expand": "environmentvariabledefinition_environmentvariablevalue($select=environmentvariablevalueid)",
    }).get("value", [])
    if not defs:
        raise SystemExit(f"La variable {schema} no existe; créala con --definir")
    d = defs[0]
    filas = d.get("environmentvariabledefinition_environmentvariablevalue", [])
    if filas:
        dv.patch(f"environmentvariablevalues({filas[0]['environmentvariablevalueid']})", {"value": valor})
        print(f"  {schema}: valor actualizado")
    else:
        dv.post("environmentvariablevalues", {
            "value": valor,
            "EnvironmentVariableDefinitionId@odata.bind": f"/environmentvariabledefinitions({d['environmentvariabledefinitionid']})",
        }, extra={"MSCRM.SolutionUniqueName": solucion})
        print(f"  {schema}: fila de valor creada")


def clave_desde_az() -> str:
    az = AZ if os.path.exists(AZ) else "az"
    r = subprocess.run([az, "functionapp", "keys", "list", "-g", "rg-ini-firma-dev", "-n", "func-ini-firma-dev", "-o", "json"],
                       capture_output=True, text=True, shell=(az == "az"))
    if r.returncode != 0:
        raise SystemExit(r.stderr.strip()[:300])
    claves = json.loads(r.stdout).get("functionKeys", {})
    if "powerautomate" not in claves:
        raise SystemExit("No existe la clave 'powerautomate': az functionapp keys set --key-type functionKeys --key-name powerautomate …")
    return claves["powerautomate"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listar", action="store_true")
    ap.add_argument("--definir", nargs=2, metavar=("SCHEMA", "DISPLAY"))
    ap.add_argument("--fijar", nargs="+", metavar="SCHEMA [VALOR]")
    ap.add_argument("--desde-az", action="store_true", help="con --fijar ini_FIRMA_ApiKey: lee la clave 'powerautomate' de az")
    ap.add_argument("--solucion", default="FirmadeDocumentos")
    a = ap.parse_args()
    dv = Dataverse()
    if a.listar:
        for v in listar(dv):
            vals = [x.get("value") for x in v.get("environmentvariabledefinition_environmentvariablevalue", [])]
            mostrado = [("<clave de %d caracteres>" % len(x)) if "ApiKey" in v["schemaname"] and x else x for x in vals]
            print(f"  {v['schemaname']:28} {v.get('displayname','')!s:32} default={v.get('defaultvalue')!r:18} valor={mostrado}")
    if a.definir:
        definir(dv, a.definir[0], a.definir[1], a.solucion)
    if a.fijar:
        schema = a.fijar[0]
        valor = clave_desde_az() if a.desde_az else (a.fijar[1] if len(a.fijar) > 1 else "")
        fijar(dv, schema, valor, a.solucion)


if __name__ == "__main__":
    main()
