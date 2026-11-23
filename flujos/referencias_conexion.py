"""Referencias de conexión de la solución: listar las que puedo leer y crear las mías.

Las referencias de cpitti (`ini_firma_sharepoint`, `ini_firma_outlook`) son registros suyos que jlmunoz
no puede ni leer. En vez de pedir que las reasigne, se crean referencias nuevas propiedad de jlmunoz,
apuntando a sus conexiones, y los flujos pasan a nombrarlas (connectionReferences del clientdata).

Uso:
  python flujos/referencias_conexion.py --listar
  python flujos/referencias_conexion.py --crear ini_firma_sp   "FIRMA · SharePoint (jlmunoz)"       shared_sharepointonline <connectionid>
  python flujos/referencias_conexion.py --crear ini_firma_o365 "FIRMA · Office 365 Outlook (jlmunoz)" shared_office365       <connectionid>

El connectionid sale de `pac connection list` (columna Id).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dataverse import Dataverse  # noqa: E402


def listar(dv: Dataverse) -> None:
    r = dv.get("connectionreferences", params={
        "$select": "connectionreferencelogicalname,connectionreferencedisplayname,connectorid,connectionid,_ownerid_value",
        "$orderby": "connectionreferencelogicalname",
    })
    for x in r.get("value", []):
        print(f"  {x['connectionreferencelogicalname']:28} {x.get('connectionreferencedisplayname','')!s:40} "
              f"{(x.get('connectorid') or '').split('/')[-1]:26} conn={x.get('connectionid')} owner={x.get('_ownerid_value')}")


def crear(dv: Dataverse, logical: str, display: str, connector: str, connection_id: str, solucion: str) -> None:
    existentes = dv.get("connectionreferences", params={
        "$select": "connectionreferenceid", "$filter": f"connectionreferencelogicalname eq '{logical}'",
    }).get("value", [])
    if existentes:
        print(f"  {logical} ya existe ({existentes[0]['connectionreferenceid']})")
        return
    r = dv.post("connectionreferences", {
        "connectionreferencelogicalname": logical,
        "connectionreferencedisplayname": display,
        "connectorid": f"/providers/Microsoft.PowerApps/apis/{connector}",
        "connectionid": connection_id,
        "description": "Plan_Firma_INI · referencia propia de jlmunoz",
    }, extra={"MSCRM.SolutionUniqueName": solucion, "Prefer": "return=representation"})
    print(f"  creada {logical} -> {r.get('connectionreferenceid')} (conexión {connection_id})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listar", action="store_true")
    ap.add_argument("--crear", nargs=4, metavar=("LOGICAL", "DISPLAY", "CONNECTOR", "CONNECTIONID"))
    ap.add_argument("--solucion", default="FirmadeDocumentos")
    a = ap.parse_args()
    dv = Dataverse()
    if a.listar:
        listar(dv)
    if a.crear:
        crear(dv, *a.crear, a.solucion)


if __name__ == "__main__":
    main()
