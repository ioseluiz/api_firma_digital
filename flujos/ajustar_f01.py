"""Aplica a F01 (Registrar envío) el diff mínimo para publicar el original en el visor del API.

Parte de flujos/FIRMA_F01_Registrar_envio.json (línea base exportada de Dataverse) y escribe el mismo
archivo con exactamente estos cambios (skill §2.2: el diff debe mostrar solo esto):

  1. parámetros nuevos ini_FIRMA_ApiUrl, ini_FIRMA_ApiKey, ini_FIRMA_AppUrl;
  2. acción `Publicar_visor` (HTTP POST {ApiUrl}/visor con el memo) tras `Propiedades_original`;
  3. `Cada_anexo` corre después de `Publicar_visor` pase lo que pase (el visor no bloquea el envío);
  4. `Pasar_a_pendiente` guarda `Url_Visor`;
  5. `Bitacora_enviado` anota si el visor quedó disponible;
  6. `Correo_gerente` enlaza a la app cuando ini_FIRMA_AppUrl está configurada.

Es idempotente: si ya está aplicado, no cambia nada.
"""

from __future__ import annotations

import json
from pathlib import Path

AQUI = Path(__file__).resolve().parent
ARCHIVO = AQUI / "FIRMA_F01_Registrar_envio.json"
API_URL = "parameters('ini_FIRMA_ApiUrl (ini_FIRMA_ApiUrl)')"
API_KEY = "parameters('ini_FIRMA_ApiKey (ini_FIRMA_ApiKey)')"
APP_URL = "parameters('ini_FIRMA_AppUrl (ini_FIRMA_AppUrl)')"
URL_VISOR = "@coalesce(actions('Publicar_visor')?['outputs']?['body']?['url_visor'], '')"


def main() -> None:
    d = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    df = d["properties"]["definition"]
    cambios = 0

    # 1. parámetros
    for nombre in ("ini_FIRMA_ApiUrl", "ini_FIRMA_ApiKey", "ini_FIRMA_AppUrl"):
        clave = f"{nombre} ({nombre})"
        if clave not in df["parameters"]:
            df["parameters"][clave] = {"defaultValue": "SIN_CONFIGURAR", "type": "String", "metadata": {"schemaName": nombre}}
            cambios += 1

    then = df["actions"]["Try"]["actions"]["Validar_adjuntos"]["actions"]

    # 2. Publicar_visor
    if "Publicar_visor" not in then:
        then["Publicar_visor"] = {
            "type": "Http",
            "inputs": {
                "method": "POST",
                "uri": f"@{{{API_URL}}}/visor",
                "headers": {"Content-Type": "application/json", "x-functions-key": f"@{API_KEY}",
                            "X-Correlation-Id": "@workflow()?['run']?['name']"},
                "body": {
                    "solicitud_id": "@triggerOutputs()?['body/ID']",
                    "codigo": "@outputs('Codigo')",
                    "pdf_base64": "@base64(body('Contenido_memo'))",
                },
                "retryPolicy": {"type": "exponential", "count": 2, "interval": "PT15S", "minimumInterval": "PT15S", "maximumInterval": "PT1M"},
            },
            "limit": {"timeout": "PT1M"},
            "runtimeConfiguration": {"secureData": {"properties": ["inputs"]}},
            "runAfter": {"Propiedades_original": ["Succeeded"]},
        }
        cambios += 1

    # 3. Cada_anexo espera al visor, pero no depende de que salga bien
    if then["Cada_anexo"]["runAfter"] != {"Publicar_visor": ["Succeeded", "Failed", "TimedOut"]}:
        then["Cada_anexo"]["runAfter"] = {"Publicar_visor": ["Succeeded", "Failed", "TimedOut"]}
        cambios += 1

    # 4. Url_Visor
    p = then["Pasar_a_pendiente"]["inputs"]["parameters"]
    if p.get("item/Url_Visor") != URL_VISOR:
        p["item/Url_Visor"] = URL_VISOR
        cambios += 1

    # 5. bitácora
    b = then["Bitacora_enviado"]["inputs"]["parameters"]
    marca = "' · visor=', if(empty(coalesce(actions('Publicar_visor')?['outputs']?['body']?['url_visor'],'')), 'no disponible', 'ok')"
    if "visor=" not in b["item/Detalle"]:
        viejo = b["item/Detalle"]
        assert viejo.startswith("@{concat(") and viejo.endswith(")}"), viejo
        b["item/Detalle"] = viejo[:-2] + ", " + marca + ")}"
        cambios += 1

    # 6. botón a la app en el correo del gerente
    c = then["Correo_gerente"]["inputs"]["parameters"]
    boton = (f"@{{if(or(empty({APP_URL}), equals({APP_URL},'SIN_CONFIGURAR')), '', "
             f"concat('<p style=\"margin:16px 0 0\"><a href=\"', {APP_URL}, '\" style=\"background:#00539B;color:#fff;padding:10px 16px;"
             f"text-decoration:none;border-radius:4px;display:inline-block\">Revisar y firmar en la app</a></p>'))}}")
    if "Revisar y firmar en la app" not in c["emailMessage/Body"]:
        cuerpo = c["emailMessage/Body"]
        pie = '<p style="margin:16px 0 0;color:#6b7280;font-size:12px">'
        assert pie in cuerpo, "no encuentro el pie del correo del gerente"
        c["emailMessage/Body"] = cuerpo.replace(pie, boton + "\n" + pie, 1)
        cambios += 1

    ARCHIVO.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{ARCHIVO.name}: {cambios} cambio(s) aplicado(s)" if cambios else f"{ARCHIVO.name}: ya estaba ajustado")


if __name__ == "__main__":
    main()
