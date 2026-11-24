"""Piezas comunes de los generadores de flujos (F01, F02, F07): constantes, acciones y correos.

Las definiciones se construyen en Python para que todos los flujos compartan, letra por letra, las
convenciones de la solución (Try/Catch, bitácora, correos con ModoPrueba, columna-marca Accion).
"""

from __future__ import annotations

from pathlib import Path

AQUI = Path(__file__).resolve().parent

SITIO = "@parameters('ini_FIRMA_SitioUrl (ini_FIRMA_SitioUrl)')"
BIBLIOTECA = "@parameters('ini_FIRMA_BibliotecaId (ini_FIRMA_BibliotecaId)')"
MODO = "parameters('ini_FIRMA_ModoPrueba (ini_FIRMA_ModoPrueba)')"
SOPORTE = "parameters('ini_FIRMA_CorreoSoporte (ini_FIRMA_CorreoSoporte)')"
API_URL = "parameters('ini_FIRMA_ApiUrl (ini_FIRMA_ApiUrl)')"
API_KEY = "parameters('ini_FIRMA_ApiKey (ini_FIRMA_ApiKey)')"
APP_URL = "parameters('ini_FIRMA_AppUrl (ini_FIRMA_AppUrl)')"
LISTA_SOLICITUDES = "767347d5-3d27-4b12-8360-f114d5c51002"
LISTA_BITACORA = "05d9b67a-a005-4826-ad13-67a622f834aa"
LISTA_ROLES = "41a2b1f3-f830-4b76-9d11-a7d466020d5a"
LISTA_PARAMETROS = "5b8a0ed7-f8c3-44da-864a-6565621accd4"
LISTA_DOCUMENTOS = "21902b20-1db8-4d91-b493-01372bfd4ee8"   # FIRMA_Solicitud_Documentos (una fila por documento a firmar)
ID = "@triggerOutputs()?['body/ID']"
ID_FILTRO = "@{triggerOutputs()?['body/ID']}"  # para $filter: una expresión en medio de un texto va entre @{...}
TITULO = "@triggerOutputs()?['body/Title']"
CODIGO = "@triggerOutputs()?['body/Codigo']"
EDITOR = "toLower(coalesce(triggerOutputs()?['body/Editor/Email'],''))"
PRUEBA = f"if(equals({MODO},'SI'),'[PRUEBA] ','')"
AUTH = "@parameters('$authentication')"

PIE = '<p style="margin:16px 0 0;color:#6b7280;font-size:12px">Firma de Documentos · INI. Correo automático, no responder.</p>'

VARIABLES_ENTORNO = ("ini_FIRMA_SitioUrl", "ini_FIRMA_BibliotecaId", "ini_FIRMA_ModoPrueba", "ini_FIRMA_CorreoSoporte",
                     "ini_FIRMA_ApiUrl", "ini_FIRMA_ApiKey", "ini_FIRMA_AppUrl")


def parametros_flujo(nombres=VARIABLES_ENTORNO) -> dict:
    p = {
        "$connections": {"defaultValue": {}, "type": "Object"},
        "$authentication": {"defaultValue": {}, "type": "SecureObject"},
    }
    for nombre in nombres:
        p[f"{nombre} ({nombre})"] = {"defaultValue": "SIN_CONFIGURAR", "type": "String", "metadata": {"schemaName": nombre}}
    return p


def referencias_conexion() -> dict:
    return {
        "shared_sharepointonline": {"runtimeSource": "embedded", "connection": {"connectionReferenceLogicalName": "ini_firma_sp"},
                                    "api": {"name": "shared_sharepointonline"}},
        "shared_office365": {"runtimeSource": "embedded", "connection": {"connectionReferenceLogicalName": "ini_firma_o365"},
                             "api": {"name": "shared_office365"}},
    }


# ---------------------------------------------------------------- HTML de correos

def fila(rotulo: str, valor: str) -> str:
    return (f'<tr><td style="padding:6px 8px;background:#f3f6f9;width:170px;font-weight:600">{rotulo}</td>'
            f'<td style="padding:6px 8px">{valor}</td></tr>')


def correo_html(titulo: str, intro: str, filas: list[tuple[str, str]], extra: str = "") -> str:
    tabla = '<table style="border-collapse:collapse;width:100%">' + "".join(fila(r, v) for r, v in filas) + "</table>"
    return ('<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1f2937;max-width:640px">\n'
            f'<div style="background:#00539B;color:#fff;padding:14px 18px;font-size:16px;font-weight:600">{titulo}</div>\n'
            '<div style="padding:16px 18px;border:1px solid #d6dde6;border-top:0">\n'
            f'<p style="margin:0 0 12px">{intro}</p>\n{tabla}\n{extra}\n{PIE}\n</div></div>')


# ---------------------------------------------------------------- acciones

def sp(operation_id: str, parametros: dict, run_after: dict | None = None) -> dict:
    return {
        "type": "OpenApiConnection",
        "inputs": {
            "host": {"apiId": "/providers/Microsoft.PowerApps/apis/shared_sharepointonline",
                     "connectionName": "shared_sharepointonline", "operationId": operation_id},
            "parameters": parametros,
            "authentication": AUTH,
        },
        "runAfter": run_after or {},
    }


def correo(to: str, subject: str, body: str, run_after: dict | None = None, cc: str | None = None,
           importance: str = "Normal", adjuntos=None, reply_to: str | None = None) -> dict:
    p = {"emailMessage/To": to, "emailMessage/Subject": subject, "emailMessage/Body": body, "emailMessage/Importance": importance}
    if cc is not None:
        p["emailMessage/Cc"] = cc
    if reply_to is not None:
        p["emailMessage/ReplyTo"] = reply_to
    if adjuntos:
        p["emailMessage/Attachments"] = adjuntos
    return {
        "type": "OpenApiConnection",
        "inputs": {
            "host": {"apiId": "/providers/Microsoft.PowerApps/apis/shared_office365",
                     "connectionName": "shared_office365", "operationId": "SendEmailV2"},
            "parameters": p,
            "authentication": AUTH,
        },
        "runAfter": run_after or {},
    }


def bitacora(evento: str, actor: str, detalle: str, run_after: dict | None = None, solicitud_id: str = ID,
             codigo: str = CODIGO) -> dict:
    return sp("PostItem", {
        "dataset": SITIO, "table": LISTA_BITACORA,
        "item/Title": evento, "item/Solicitud_ID_Num": solicitud_id, "item/Codigo": codigo,
        "item/Evento": evento, "item/Actor_Email": actor, "item/Detalle": detalle, "item/Fecha": "@utcNow()",
    }, run_after)


def patch_solicitud(campos: dict, run_after: dict | None = None, solicitud_id: str = ID, titulo: str = TITULO) -> dict:
    return sp("PatchItem", {"dataset": SITIO, "table": LISTA_SOLICITUDES, "id": solicitud_id, "item/Title": titulo, **campos}, run_after)


def patch_documento(id_fila: str, campos: dict, run_after: dict | None = None, solicitud_id: str = ID) -> dict:
    """Actualiza una fila de FIRMA_Solicitud_Documentos. Solicitud_ID_Num es obligatoria en la lista y PatchItem
    exige las obligatorias aunque no cambien; Title también (se pasa en campos)."""
    return sp("PatchItem", {"dataset": SITIO, "table": LISTA_DOCUMENTOS, "id": id_fila,
                            "item/Solicitud_ID_Num": solicitud_id, **campos}, run_after)


def query(desde: str, donde: str, run_after: dict | None = None) -> dict:
    return {"type": "Query", "inputs": {"from": desde, "where": donde}, "runAfter": run_after or {}}


def compose(valor, run_after: dict | None = None) -> dict:
    return {"type": "Compose", "inputs": valor, "runAfter": run_after or {}}


def ok(*nombres: str) -> dict:
    return {n: ["Succeeded"] for n in nombres}


def despues(nombre: str, *estados: str) -> dict:
    """runAfter con varios estados: despues('X', 'Succeeded', 'Failed', 'TimedOut')."""
    return {nombre: list(estados)}


def parametro(valor: str) -> dict:
    # Dentro del Scope no se puede apuntar a Leer_parametros (está fuera): van al inicio del Try.
    return query("@body('Leer_parametros')?['value']", f"@equals(item()?['Clave'],'{valor}')")


def scope(acciones: dict, run_after: dict | None = None) -> dict:
    return {"type": "Scope", "actions": acciones, "runAfter": run_after or {}}


def foreach(sobre: str, acciones: dict, run_after: dict | None = None, concurrencia: int = 1) -> dict:
    """Bucle con concurrencia 1 por omisión: dentro se usan variables (contadores, acumuladores) sin carreras."""
    return {"type": "Foreach", "foreach": sobre, "actions": acciones,
            "runtimeConfiguration": {"concurrency": {"repetitions": concurrencia}}, "runAfter": run_after or {}}


def condicion(expresion: dict, entonces: dict, si_no: dict | None = None, run_after: dict | None = None) -> dict:
    return {"type": "If", "expression": expresion, "actions": entonces, "else": {"actions": si_no or {}}, "runAfter": run_after or {}}


def variable_inicial(nombre: str, tipo: str, valor, run_after: dict | None = None) -> dict:
    """Solo en el nivel raíz del flujo (InitializeVariable no se admite dentro de Scope/Foreach)."""
    return {"type": "InitializeVariable", "inputs": {"variables": [{"name": nombre, "type": tipo, "value": valor}]},
            "runAfter": run_after or {}}


def fijar_variable(nombre: str, valor, run_after: dict | None = None) -> dict:
    return {"type": "SetVariable", "inputs": {"name": nombre, "value": valor}, "runAfter": run_after or {}}


def incrementar(nombre: str, valor: int = 1, run_after: dict | None = None) -> dict:
    return {"type": "IncrementVariable", "inputs": {"name": nombre, "value": valor}, "runAfter": run_after or {}}


def append_array(nombre: str, valor, run_after: dict | None = None) -> dict:
    return {"type": "AppendToArrayVariable", "inputs": {"name": nombre, "value": valor}, "runAfter": run_after or {}}


def http_api(ruta: str, body: dict, run_after: dict | None = None, metodo: str = "POST",
             retry: dict | None = None, timeout: str = "PT1M") -> dict:
    """Llamada al API de firma con la clave de función y el id de correlación de la ejecución."""
    return {
        "type": "Http",
        "inputs": {
            "method": metodo,
            "uri": f"@{{{API_URL}}}{ruta}",
            "headers": {"Content-Type": "application/json", "x-functions-key": f"@{API_KEY}",
                        "X-Correlation-Id": "@workflow()?['run']?['name']"},
            "body": body,
            "retryPolicy": retry or {"type": "exponential", "count": 2, "interval": "PT15S", "minimumInterval": "PT15S", "maximumInterval": "PT1M"},
        },
        "limit": {"timeout": timeout},
        "runtimeConfiguration": {"secureData": {"properties": ["inputs"]}},
        "runAfter": run_after or {},
    }


def trigger_sharepoint(condicion_expr: str) -> dict:
    """Disparador 'Cuando se modifica un elemento' de FIRMA_Solicitudes, cada minuto, con filtro y concurrencia 1."""
    return {
        "Al_crear_o_modificar_solicitud": {
            "type": "OpenApiConnection",
            "inputs": {
                "host": {"apiId": "/providers/Microsoft.PowerApps/apis/shared_sharepointonline",
                         "connectionName": "shared_sharepointonline", "operationId": "GetOnUpdatedItems"},
                "parameters": {"dataset": SITIO, "table": LISTA_SOLICITUDES},
                "authentication": AUTH,
            },
            "recurrence": {"interval": 1, "frequency": "Minute"},
            "splitOn": "@triggerOutputs()?['body/value']",
            "conditions": [{"expression": condicion_expr}],
            "runtimeConfiguration": {"concurrency": {"runs": 1}},
        }
    }


def flujo(nombre: str, triggers: dict, actions: dict, parametros: dict | None = None) -> dict:
    return {
        "nombre": nombre,
        "properties": {
            "connectionReferences": referencias_conexion(),
            "definition": {
                "$schema": "https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#",
                "contentVersion": "1.0.0.0",
                "parameters": parametros or parametros_flujo(),
                "triggers": triggers,
                "actions": actions,
            },
            "templateName": "",
        },
        "schemaVersion": "1.0.0.0",
    }
