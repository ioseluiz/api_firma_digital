"""Valida una definición de flujo (clientdata) antes de subirla: los nombres de parámetro de cada acción
contra el swagger REAL del conector en el tenant, y la coherencia interna de la definición.

Por qué: un nombre de parámetro mal escrito no falla al importar ni al subir; falla al ENCENDER el flujo,
uno por intento (`WorkflowOperationParametersExtraParameter`). Ver skill power-platform-acp §2.15b.

Uso:
  python flujos/validar_parametros.py flujos/FIRMA_F02_Aprobar_y_firmar.json [--actualizar] [--env <envId>]

El swagger se cachea en flujos/swagger/<connector>.json (gitignored). `--actualizar` lo vuelve a bajar con
`pac connectivity get-connector-by-id`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
SWAGGER = AQUI / "swagger"
ENV_DEVTEST = "e42f9227-da75-e921-a616-9693cdb45315"
PAC = os.path.join(os.environ.get("USERPROFILE", ""), ".dotnet", "tools", "pac.exe")

REF_RE = re.compile(r"\b(body|outputs|actions|items|result)\('([^']+)'\)")
PARAM_RE = re.compile(r"parameters\('([^']+)'\)")


def swagger_de(connector: str, env: str, actualizar: bool) -> dict:
    SWAGGER.mkdir(exist_ok=True)
    destino = SWAGGER / f"{connector}.json"
    if destino.exists() and not actualizar:
        return json.loads(destino.read_text(encoding="utf-8"))
    pac = PAC if os.path.exists(PAC) else "pac"
    r = subprocess.run([pac, "connectivity", "get-connector-by-id", "--connector-id", connector, "--environment", env,
                        "--filter", f"environment eq '{env}'"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"pac connectivity falló para {connector}: {r.stderr.strip()[:400]}")
    inicio = r.stdout.find("{")
    if inicio < 0:
        raise SystemExit(f"pac no devolvió JSON para {connector}:\n{r.stdout[:400]}")
    datos = json.loads(r.stdout[inicio:])
    # pac envuelve el swagger en properties.swagger en algunas versiones
    sw = datos.get("properties", {}).get("swagger") or datos.get("swagger") or datos
    destino.write_text(json.dumps(sw, ensure_ascii=False, indent=1), encoding="utf-8")
    return sw


def operaciones(sw: dict) -> dict[str, dict]:
    ops = {}
    for ruta, metodos in sw.get("paths", {}).items():
        for metodo, op in metodos.items():
            if isinstance(op, dict) and "operationId" in op:
                ops[op["operationId"]] = op
    return ops


def resolver(sw: dict, esquema: dict) -> dict:
    while isinstance(esquema, dict) and "$ref" in esquema:
        ref = esquema["$ref"].split("/")[1:]
        nodo = sw
        for parte in ref:
            nodo = nodo[parte]
        esquema = nodo
    return esquema


def aplanar(sw: dict, prefijo: str, esquema: dict, salida: set, dinamico: set, profundidad: int = 0) -> None:
    esquema = resolver(sw, esquema)
    if "x-ms-dynamic-schema" in esquema or "x-ms-dynamic-properties" in esquema:
        dinamico.add(prefijo)
        return
    for nombre, sub in (esquema.get("properties") or {}).items():
        sub = resolver(sw, sub)
        ruta = f"{prefijo}/{nombre}"
        salida.add(ruta)
        if sub.get("type") == "object" or "properties" in sub or "$ref" in sub:
            if profundidad < 6:
                aplanar(sw, ruta, sub, salida, dinamico, profundidad + 1)


def parametros_validos(sw: dict, op: dict) -> tuple[set, set]:
    """(nombres válidos, prefijos dinámicos no verificables)."""
    validos, dinamicos = set(), set()
    for p in op.get("parameters", []):
        p = resolver(sw, p)
        if p.get("in") == "body":
            validos.add(p["name"])
            aplanar(sw, p["name"], p.get("schema", {}), validos, dinamicos)
        else:
            validos.add(p["name"])
    return validos, dinamicos


def recorrer(acciones: dict, ruta: str = ""):
    for nombre, a in acciones.items():
        yield ruta + nombre, a, acciones
        for sub in (a.get("actions"), (a.get("else") or {}).get("actions")):
            if sub:
                yield from recorrer(sub, ruta + nombre + "/")
        for caso in (a.get("cases") or {}).values():
            if caso.get("actions"):
                yield from recorrer(caso["actions"], ruta + nombre + "/")
        if (a.get("default") or {}).get("actions"):
            yield from recorrer(a["default"]["actions"], ruta + nombre + "/")


def validar(archivo: Path, env: str, actualizar: bool) -> list[str]:
    d = json.loads(archivo.read_text(encoding="utf-8"))
    props = d["properties"]
    df = props["definition"]
    hallazgos: list[str] = []
    refs_conexion = {k: v.get("connection", {}).get("connectionReferenceLogicalName") for k, v in props.get("connectionReferences", {}).items()}
    swaggers: dict[str, dict] = {}

    def conector(host: dict) -> str:
        return host.get("connectionName") or host.get("apiId", "").split("/")[-1]

    nodos = [(n, t, df["triggers"]) for n, t in df.get("triggers", {}).items()] + list(recorrer(df.get("actions", {})))
    nombres_accion = {n.split("/")[-1] for n, _, _ in nodos}
    declarados = set(df.get("parameters", {}).keys())
    texto = json.dumps(df, ensure_ascii=False)

    for ruta, a, hermanos in nodos:
        tipo = a.get("type")
        if tipo == "OpenApiConnection":
            host = a["inputs"]["host"]
            con = conector(host)
            if con not in refs_conexion:
                hallazgos.append(f"{ruta}: conector {con!r} sin referencia de conexión en connectionReferences")
            if con not in swaggers:
                swaggers[con] = swagger_de(con, env, actualizar)
            ops = operaciones(swaggers[con])
            op_id = host.get("operationId")
            if op_id not in ops:
                hallazgos.append(f"{ruta}: operationId {op_id!r} no existe en {con}")
                continue
            validos, dinamicos = parametros_validos(swaggers[con], ops[op_id])
            for p in a["inputs"].get("parameters", {}):
                if p in validos:
                    continue
                if any(p == pref or p.startswith(pref + "/") for pref in dinamicos):
                    continue  # esquema dinámico (p. ej. item/<columna> de SharePoint): no verificable aquí
                hallazgos.append(f"{ruta}: parámetro {p!r} no declarado en {con}.{op_id}")
        for dep, estados in (a.get("runAfter") or {}).items():
            if dep not in hermanos:
                hallazgos.append(f"{ruta}: runAfter apunta a {dep!r}, que no es hermano")
            for est in estados:
                if est not in ("Succeeded", "Failed", "Skipped", "TimedOut"):
                    hallazgos.append(f"{ruta}: estado de runAfter inválido {est!r}")

    for fn, nombre in REF_RE.findall(texto):
        if fn == "items":
            continue  # items('Bucle') se verifica como acción abajo
        if nombre not in nombres_accion:
            hallazgos.append(f"{fn}('{nombre}') referencia una acción inexistente")
    for nombre in set(PARAM_RE.findall(texto)):
        if nombre not in declarados:
            hallazgos.append(f"parameters('{nombre}') no está declarado en definition.parameters")
    if texto.count("@{") != texto.count("}") - texto.count("}}") and texto.count("@{") > texto.count("}"):
        hallazgos.append("hay más '@{' que '}' en la definición")
    for nombre, p in df.get("parameters", {}).items():
        if nombre.startswith("$"):
            continue
        if not (p.get("metadata") or {}).get("schemaName"):
            hallazgos.append(f"parámetro {nombre!r} sin metadata.schemaName (el flujo usaría el defaultValue en silencio)")
    return hallazgos


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archivo", nargs="+")
    ap.add_argument("--env", default=ENV_DEVTEST)
    ap.add_argument("--actualizar", action="store_true", help="volver a bajar los swagger")
    a = ap.parse_args()
    total = 0
    for f in a.archivo:
        h = validar(Path(f), a.env, a.actualizar)
        print(f"== {f}: {'sin hallazgos' if not h else str(len(h)) + ' hallazgo(s)'}")
        for x in h:
            print("   -", x)
        total += len(h)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
