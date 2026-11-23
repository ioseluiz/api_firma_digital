"""Genera flujos/FIRMA_F02_Aprobar_y_firmar.json: el flujo que llama al API de firma.

Se construye en Python en lugar de escribir el JSON a mano para que comparta, letra por letra, las
convenciones de F01/F03/F04 (Try/Catch, bitácora, correos con ModoPrueba, Marcar_accion_tomada).
Después: `python flujos/validar_parametros.py flujos/FIRMA_F02_Aprobar_y_firmar.json` y
`python flujos/subir_flujo.py --crear ... --si` (primera vez) o `--flujo ... --si --activar`.

Disparador: Accion = 'Aprobar', Accion_Fecha vacía, Estado 'Pendiente de firma' o 'Error de firma'
(reintento). Concurrencia 1 (irreversible una vez guardado, skill §2.12: decidido).
"""

from __future__ import annotations

import json
from pathlib import Path

AQUI = Path(__file__).resolve().parent
DESTINO = AQUI / "FIRMA_F02_Aprobar_y_firmar.json"

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
ID = "@triggerOutputs()?['body/ID']"
TITULO = "@triggerOutputs()?['body/Title']"
CODIGO = "@triggerOutputs()?['body/Codigo']"
EDITOR = "toLower(coalesce(triggerOutputs()?['body/Editor/Email'],''))"
PRUEBA = f"if(equals({MODO},'SI'),'[PRUEBA] ','')"
AUTH = "@parameters('$authentication')"

PIE = '<p style="margin:16px 0 0;color:#6b7280;font-size:12px">Firma de Documentos · INI. Correo automático, no responder.</p>'


def fila(rotulo: str, valor: str) -> str:
    return (f'<tr><td style="padding:6px 8px;background:#f3f6f9;width:170px;font-weight:600">{rotulo}</td>'
            f'<td style="padding:6px 8px">{valor}</td></tr>')


def correo_html(titulo: str, intro: str, filas: list[tuple[str, str]], extra: str = "") -> str:
    tabla = '<table style="border-collapse:collapse;width:100%">' + "".join(fila(r, v) for r, v in filas) + "</table>"
    return ('<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1f2937;max-width:640px">\n'
            f'<div style="background:#00539B;color:#fff;padding:14px 18px;font-size:16px;font-weight:600">{titulo}</div>\n'
            '<div style="padding:16px 18px;border:1px solid #d6dde6;border-top:0">\n'
            f'<p style="margin:0 0 12px">{intro}</p>\n{tabla}\n{extra}\n{PIE}\n</div></div>')


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
           importance: str = "Normal", adjuntos: list | None = None) -> dict:
    p = {"emailMessage/To": to, "emailMessage/Subject": subject, "emailMessage/Body": body, "emailMessage/Importance": importance}
    if cc is not None:
        p["emailMessage/Cc"] = cc
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


def bitacora(evento: str, actor: str, detalle: str, run_after: dict | None = None) -> dict:
    return sp("PostItem", {
        "dataset": SITIO, "table": LISTA_BITACORA,
        "item/Title": evento, "item/Solicitud_ID_Num": ID, "item/Codigo": CODIGO,
        "item/Evento": evento, "item/Actor_Email": actor, "item/Detalle": detalle, "item/Fecha": "@utcNow()",
    }, run_after)


def patch_solicitud(campos: dict, run_after: dict | None = None) -> dict:
    return sp("PatchItem", {"dataset": SITIO, "table": LISTA_SOLICITUDES, "id": ID, "item/Title": TITULO, **campos}, run_after)


def query(desde: str, donde: str, run_after: dict | None = None) -> dict:
    return {"type": "Query", "inputs": {"from": desde, "where": donde}, "runAfter": run_after or {}}


def compose(valor, run_after: dict | None = None) -> dict:
    return {"type": "Compose", "inputs": valor, "runAfter": run_after or {}}


def ok(*nombres: str) -> dict:
    return {n: ["Succeeded"] for n in nombres}


def parametro(valor: str) -> dict:
    # Dentro del Scope no se puede apuntar a Leer_parametros (está fuera): van al inicio del Try.
    return query("@body('Leer_parametros')?['value']", f"@equals(item()?['Clave'],'{valor}')")


def construir() -> dict:
    parametros = {
        "$connections": {"defaultValue": {}, "type": "Object"},
        "$authentication": {"defaultValue": {}, "type": "SecureObject"},
    }
    for nombre in ("ini_FIRMA_SitioUrl", "ini_FIRMA_BibliotecaId", "ini_FIRMA_ModoPrueba", "ini_FIRMA_CorreoSoporte",
                   "ini_FIRMA_ApiUrl", "ini_FIRMA_ApiKey", "ini_FIRMA_AppUrl"):
        parametros[f"{nombre} ({nombre})"] = {"defaultValue": "SIN_CONFIGURAR", "type": "String", "metadata": {"schemaName": nombre}}

    trigger = {
        "Al_crear_o_modificar_solicitud": {
            "type": "OpenApiConnection",
            "recurrence": {"interval": 1, "frequency": "Minute"},
            "splitOn": "@triggerOutputs()?['body/value']",
            "conditions": [{"expression": (
                "@and(equals(triggerOutputs()?['body/Accion'],'Aprobar'), empty(triggerOutputs()?['body/Accion_Fecha']), "
                "or(equals(triggerOutputs()?['body/Estado/Value'],'Pendiente de firma'), equals(triggerOutputs()?['body/Estado/Value'],'Error de firma')))"
            )}],
            "inputs": {
                "host": {"apiId": "/providers/Microsoft.PowerApps/apis/shared_sharepointonline",
                         "connectionName": "shared_sharepointonline", "operationId": "GetOnUpdatedItems"},
                "parameters": {"dataset": SITIO, "table": LISTA_SOLICITUDES},
                "authentication": AUTH,
            },
            "runtimeConfiguration": {"concurrency": {"runs": 1}},
        }
    }

    # ---------- Try ----------
    es_reintento = "equals(triggerOutputs()?['body/Estado/Value'],'Error de firma')"
    detalle_error = (
        "@concat('HTTP ', coalesce(string(actions('Llamar_api_firmar')?['outputs']?['statusCode']),'—'), ' ', "
        "coalesce(actions('Llamar_api_firmar')?['outputs']?['body']?['error'],''), ': ', "
        "coalesce(actions('Llamar_api_firmar')?['outputs']?['body']?['mensaje'],''), ' · corr=', "
        "coalesce(actions('Llamar_api_firmar')?['outputs']?['body']?['correlation_id'],''), ' · ', "
        "substring(string(result('Try')), 0, min(length(string(result('Try'))), 2500)))"
    )
    enlace_app = (f"@{{if(or(empty({APP_URL}), equals({APP_URL},'SIN_CONFIGURAR')), '', "
                  f"concat('<p style=\"margin:16px 0 0\"><a href=\"', {APP_URL}, '\" style=\"background:#00539B;color:#fff;padding:10px 16px;"
                  f"text-decoration:none;border-radius:4px;display:inline-block\">Abrir la app INI Firma</a></p>'))}}")
    carpeta_html = "<p style=\"margin:12px 0 0\"><a href=\"@{triggerOutputs()?['body/Carpeta_Url']}\">Abrir la carpeta del documento en SharePoint</a></p>"

    aprobar_then = {
        "Bitacora_aprobado": bitacora(
            "Aprobado", f"@{EDITOR}",
            "@concat('Aprobado por ', outputs('Firmante')?['Title'], ' ', coalesce(outputs('Firmante')?['Nombre_Firma'],''), "
            "if(empty(coalesce(triggerOutputs()?['body/Comentario_Gerente'],'')), '', concat(' · Comentario: ', triggerOutputs()?['body/Comentario_Gerente'])))",
        ),
        "Obtener_original": sp("GetFileContentByPath", {
            "dataset": SITIO, "path": "@concat(outputs('Carpeta_relativa'), '/Original.pdf')", "inferContentType": True,
        }, ok("Bitacora_aprobado")),
        "Fecha_firma_local": compose("@convertTimeZone(utcNow(),'UTC','SA Pacific Standard Time','yyyy-MM-ddTHH:mm:ss')", ok("Obtener_original")),
        "Llamar_api_firmar": {
            "type": "Http",
            "inputs": {
                "method": "POST",
                "uri": f"@{{{API_URL}}}/firmar",
                "headers": {"Content-Type": "application/json", "x-functions-key": f"@{API_KEY}",
                            "X-Correlation-Id": "@workflow()?['run']?['name']"},
                "body": {
                    "solicitud_id": ID,
                    "codigo": CODIGO,
                    "pdf_base64": "@base64(body('Obtener_original'))",
                    "firmante": {
                        "nombre": "@coalesce(outputs('Firmante')?['Nombre_Firma'],'')",
                        "cargo": "@coalesce(outputs('Firmante')?['Cargo_Firma'],'')",
                        "email": "@toLower(coalesce(outputs('Firmante')?['Email'],''))",
                        "imagen_id": "@outputs('Imagen_Id')",
                    },
                    "fecha_firma": "@outputs('Fecha_firma_local')",
                    "posicion": "@outputs('Posicion')",
                    "sello": {"texto": "@equals(toUpper(coalesce(first(body('Param_Sello'))?['Valor'],'NO')),'SI')", "codigo_verificacion": True},
                },
                "retryPolicy": {"type": "exponential", "count": 4, "interval": "PT20S", "minimumInterval": "PT20S", "maximumInterval": "PT5M"},
            },
            "limit": {"timeout": "PT2M"},
            "runtimeConfiguration": {"secureData": {"properties": ["inputs"]}},
            "runAfter": ok("Fecha_firma_local"),
        },
        "Analizar_respuesta": {
            "type": "ParseJson",
            "inputs": {"content": "@body('Llamar_api_firmar')",
                       "schema": json.loads((AQUI.parent / "docs" / "esquema_respuesta_ok.json").read_text(encoding="utf-8"))},
            "runAfter": ok("Llamar_api_firmar"),
        },
        "Crear_firmado": sp("CreateFile", {
            "dataset": SITIO, "folderPath": "@outputs('Carpeta_relativa')", "name": "Firmado.pdf",
            "body": "@base64ToBinary(body('Analizar_respuesta')?['pdf_firmado_base64'])",
        }, ok("Analizar_respuesta")),
        "Propiedades_firmado": sp("PatchFileItem", {
            "dataset": SITIO, "table": BIBLIOTECA, "id": "@body('Crear_firmado')?['ItemId']",
            "item/Solicitud_ID_Num": ID, "item/Codigo": CODIGO, "item/Tipo_Archivo/Value": "Firmado",
        }, ok("Crear_firmado")),
        "Pasar_a_firmado": patch_solicitud({
            "item/Estado/Value": "Firmado",
            "item/Fecha_Firma": "@utcNow()",
            "item/Firmado_Por_Email": "@toLower(coalesce(outputs('Firmante')?['Email'], triggerOutputs()?['body/Editor/Email'], ''))",
            "item/Firmado_Por_Nombre": "@coalesce(outputs('Firmante')?['Nombre_Firma'], triggerOutputs()?['body/Editor/DisplayName'], '')",
            "item/Hash_Firmado": "@body('Analizar_respuesta')?['sha256']",
            "item/Codigo_Verificacion": "@body('Analizar_respuesta')?['codigo_verificacion']",
            "item/Url_Firmado": "@concat(triggerOutputs()?['body/Carpeta_Url'], '/Firmado.pdf')",
            "item/Url_Visor": "",
            "item/Error_Detalle": "",
        }, ok("Propiedades_firmado")),
        "Bitacora_firmado": bitacora(
            "Firmado", "@toLower(coalesce(outputs('Firmante')?['Email'], triggerOutputs()?['body/Editor/Email'], ''))",
            "@concat('Firmado.pdf · pág ', string(body('Analizar_respuesta')?['pagina_firmada']), '/', string(body('Analizar_respuesta')?['paginas']), "
            "' · ancla=', string(body('Analizar_respuesta')?['ancla_encontrada']), ' · sha256=', body('Analizar_respuesta')?['sha256'], "
            "' · verif=', body('Analizar_respuesta')?['codigo_verificacion'], ' · ', string(body('Analizar_respuesta')?['duracion_ms']), ' ms')",
            ok("Pasar_a_firmado"),
        ),
        "Correo_solicitante": correo(
            to=f"@{{if(equals({MODO},'SI'), {SOPORTE}, coalesce(triggerOutputs()?['body/Solicitante_Email'], triggerOutputs()?['body/Solicitante/Email'], triggerOutputs()?['body/Author/Email']))}}",
            cc=f"@{{if(equals({MODO},'SI'), '', coalesce(first(body('Filtrar_Secretaria'))?['Email'], {SOPORTE}))}}",
            subject=f"@{{{PRUEBA}}}Documento firmado: @{{triggerOutputs()?['body/Title']}} (@{{triggerOutputs()?['body/Codigo']}})",
            body=correo_html(
                "El documento fue firmado",
                "El gerente firmó el documento. Se adjunta el PDF firmado y queda guardado en la carpeta de la solicitud.",
                [("Código", "@{triggerOutputs()?['body/Codigo']}"),
                 ("Título", "@{triggerOutputs()?['body/Title']}"),
                 ("Firmado por", "@{coalesce(outputs('Firmante')?['Nombre_Firma'], triggerOutputs()?['body/Editor/DisplayName'])}"),
                 ("Fecha de firma", "@{outputs('Fecha_firma_local')}"),
                 ("Código de verificación", "@{body('Analizar_respuesta')?['codigo_verificacion']}"),
                 ("SHA-256", "@{body('Analizar_respuesta')?['sha256']}"),
                 ("Comentario del gerente", "@{coalesce(triggerOutputs()?['body/Comentario_Gerente'], '—')}")],
                carpeta_html + "\n" + enlace_app,
            ),
            adjuntos=[{"Name": "@{triggerOutputs()?['body/Codigo']}_firmado.pdf",
                       "ContentBytes": "@base64ToBinary(body('Analizar_respuesta')?['pdf_firmado_base64'])"}],
            run_after=ok("Bitacora_firmado"),
        ),
        "Correo_firmante": correo(
            to=f"@{{if(equals({MODO},'SI'), {SOPORTE}, coalesce(triggerOutputs()?['body/Editor/Email'], {SOPORTE}))}}",
            subject=f"@{{{PRUEBA}}}Firma registrada: @{{triggerOutputs()?['body/Title']}} (@{{triggerOutputs()?['body/Codigo']}})",
            body=correo_html(
                "Firma registrada",
                "El documento quedó firmado y se entregó al solicitante.",
                [("Código", "@{triggerOutputs()?['body/Codigo']}"),
                 ("Título", "@{triggerOutputs()?['body/Title']}"),
                 ("Solicitante", "@{coalesce(triggerOutputs()?['body/Solicitante_Nombre'], triggerOutputs()?['body/Solicitante/DisplayName'], triggerOutputs()?['body/Author/DisplayName'])}"),
                 ("Código de verificación", "@{body('Analizar_respuesta')?['codigo_verificacion']}")],
                carpeta_html,
            ),
            run_after=ok("Correo_solicitante"),
        ),
    }

    aprobar_else = {
        "Motivo_invalido": compose(
            "@if(equals(length(body('Firmantes_autorizados')),0), "
            f"if(and({es_reintento}, greater(length(body('Administradores')),0)), '', "
            f"concat('Acción no autorizada: ', {EDITOR}, ' no es gerente ni suplente activo.')), "
            "if(empty(outputs('Imagen_Id')), 'El firmante no tiene imagen de firma (Imagen_Firma_Id) en FIRMA_Roles.', "
            "'La solicitud no tiene carpeta (Carpeta_Url) dentro del sitio.'))"
        ),
        "Devolver_a_pendiente": patch_solicitud({
            "item/Estado/Value": "Pendiente de firma", "item/Accion": "Revisar", "item/Error_Detalle": "@outputs('Motivo_invalido')",
        }, ok("Motivo_invalido")),
        "Bitacora_invalido": bitacora("Aprobación no válida", f"@{EDITOR}", "@outputs('Motivo_invalido')", ok("Devolver_a_pendiente")),
        "Correo_soporte_invalido": correo(
            to=f"@{{{SOPORTE}}}",
            subject="[FIRMA] Aprobación no válida · @{triggerOutputs()?['body/Codigo']}",
            body=correo_html(
                "Aprobación no válida",
                "Alguien marcó Aprobar en una solicitud, pero la validación no pasó. La solicitud vuelve a Pendiente de firma.",
                [("Solicitud", "@{triggerOutputs()?['body/ID']} · @{triggerOutputs()?['body/Title']}"),
                 ("Quién", "@{triggerOutputs()?['body/Editor/Email']}"),
                 ("Motivo", "@{outputs('Motivo_invalido')}")],
            ),
            run_after=ok("Bitacora_invalido"),
        ),
    }

    try_actions = {
        "Firmantes_autorizados": query(
            "@body('Leer_roles')?['value']",
            "@and(or(equals(item()?['Title'],'Gerente'),equals(item()?['Title'],'Suplente')), "
            f"equals(toLower(coalesce(item()?['Email'],'')), {EDITOR}))",
        ),
        "Administradores": query(
            "@body('Leer_roles')?['value']",
            f"@and(equals(item()?['Title'],'Administrador'), equals(toLower(coalesce(item()?['Email'],'')), {EDITOR}))",
        ),
        "Firmante": compose(
            "@if(greater(length(body('Firmantes_autorizados')),0), first(body('Firmantes_autorizados')), first(body('Filtrar_Gerente')))",
            ok("Firmantes_autorizados", "Administradores"),
        ),
        "Imagen_Id": compose(
            "@coalesce(outputs('Firmante')?['Imagen_Firma_Id'], if(equals(outputs('Firmante')?['Title'],'Gerente'), 'gerente.png', ''))",
            ok("Firmante"),
        ),
        "Carpeta_relativa": compose(
            f"@replace(coalesce(triggerOutputs()?['body/Carpeta_Url'],''), {SITIO[1:]}, '')",
            ok("Firmante"),
        ),
        "Param_X": parametro("Firma_X_mm"),
        "Param_Y": parametro("Firma_Y_mm"),
        "Param_Ancho": parametro("Firma_Ancho_mm"),
        "Param_Ancla": parametro("Firma_Ancla_Texto"),
        "Param_Sello": parametro("Texto_Sello"),
        "Posicion": compose({
            "pagina": "@int(coalesce(triggerOutputs()?['body/Pagina_Firma'], 0))",
            "x_mm": "@float(coalesce(first(body('Param_X'))?['Valor'], '120'))",
            "y_mm": "@float(coalesce(first(body('Param_Y'))?['Valor'], '40'))",
            "ancho_mm": "@float(coalesce(first(body('Param_Ancho'))?['Valor'], '50'))",
            "ancla_texto": "@coalesce(first(body('Param_Ancla'))?['Valor'], '')",
            "ancla_alineacion": "centro",
            "si_no_hay_ancla": "posicion_fija",
        }, ok("Param_X", "Param_Y", "Param_Ancho", "Param_Ancla", "Param_Sello")),
        "Validar_aprobacion": {
            "type": "If",
            "expression": {"and": [
                {"or": [
                    {"greater": ["@length(body('Firmantes_autorizados'))", 0]},
                    {"and": [{"equals": ["@triggerOutputs()?['body/Estado/Value']", "Error de firma"]},
                             {"greater": ["@length(body('Administradores'))", 0]}]},
                ]},
                {"not": {"equals": ["@outputs('Imagen_Id')", ""]}},
                {"not": {"equals": ["@outputs('Carpeta_relativa')", "@coalesce(triggerOutputs()?['body/Carpeta_Url'],'')"]}},
            ]},
            "actions": aprobar_then,
            "else": {"actions": aprobar_else},
            "runAfter": ok("Imagen_Id", "Carpeta_relativa", "Posicion"),
        },
    }

    catch_actions = {
        "Detalle_error": compose(detalle_error),
        "Marcar_error": patch_solicitud({
            "item/Estado/Value": "Error de firma", "item/Accion": "Error", "item/Error_Detalle": "@outputs('Detalle_error')",
        }, ok("Detalle_error")),
        "Bitacora_error": bitacora("Error en F02 Firmar", f"@{EDITOR}", "@outputs('Detalle_error')", ok("Marcar_error")),
        "Correo_soporte": correo(
            to=f"@{{{SOPORTE}}}",
            subject="[FIRMA] Error en F02 Firmar · solicitud @{triggerOutputs()?['body/ID']}",
            body=correo_html(
                "Error en F02 Aprobar y firmar",
                "El flujo falló. La solicitud queda en «Error de firma»; corrige la causa y vuelve a aprobar desde la app (Reintentar).",
                [("Solicitud", "@{triggerOutputs()?['body/ID']} · @{triggerOutputs()?['body/Title']}"),
                 ("Código", "@{triggerOutputs()?['body/Codigo']}"),
                 ("Ejecución", "@{workflow()?['run']?['name']}"),
                 ("Detalle", "@{outputs('Detalle_error')}")],
            ),
            importance="High",
            run_after=ok("Bitacora_error"),
        ),
    }

    actions = {
        "Marcar_accion_tomada": patch_solicitud({
            "item/Accion_Fecha": "@utcNow()", "item/Estado/Value": "Firmando", "item/Error_Detalle": "",
        }),
        "Leer_roles": sp("GetItems", {"dataset": SITIO, "table": LISTA_ROLES, "$filter": "Activo eq 1", "$orderby": "Title", "$top": 50},
                         ok("Marcar_accion_tomada")),
        "Leer_parametros": sp("GetItems", {"dataset": SITIO, "table": LISTA_PARAMETROS, "$orderby": "Clave", "$top": 50},
                              ok("Marcar_accion_tomada")),
        "Filtrar_Gerente": query("@body('Leer_roles')?['value']", "@equals(item()?['Title'],'Gerente')", ok("Leer_roles")),
        "Filtrar_Secretaria": query("@body('Leer_roles')?['value']", "@equals(item()?['Title'],'Secretaria')", ok("Leer_roles")),
        "Try": {"type": "Scope", "actions": try_actions, "runAfter": ok("Filtrar_Gerente", "Filtrar_Secretaria", "Leer_parametros")},
        "Catch": {"type": "Scope", "actions": catch_actions, "runAfter": {"Try": ["Failed", "TimedOut"]}},
        "Borrar_visor": {
            "type": "Http",
            "inputs": {
                "method": "DELETE",
                "uri": f"@{{{API_URL}}}/visor/@{{triggerOutputs()?['body/Codigo']}}",
                "headers": {"x-functions-key": f"@{API_KEY}", "X-Correlation-Id": "@workflow()?['run']?['name']"},
                "retryPolicy": {"type": "none"},
            },
            "limit": {"timeout": "PT30S"},
            "runtimeConfiguration": {"secureData": {"properties": ["inputs"]}},
            "runAfter": {"Try": ["Succeeded"]},
        },
        # Que un fallo al borrar el visor no marque la ejecución como fallida: el documento ya está firmado.
        "Limpieza_terminada": compose("ok", {"Borrar_visor": ["Succeeded", "Failed", "TimedOut", "Skipped"]}),
    }

    definicion = {
        "$schema": "https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#",
        "contentVersion": "1.0.0.0",
        "parameters": parametros,
        "triggers": trigger,
        "actions": actions,
    }
    return {
        "nombre": "FIRMA F02 · Aprobar y firmar",
        "properties": {
            "connectionReferences": {
                "shared_sharepointonline": {"runtimeSource": "embedded", "connection": {"connectionReferenceLogicalName": "ini_firma_sharepoint"},
                                            "api": {"name": "shared_sharepointonline"}},
                "shared_office365": {"runtimeSource": "embedded", "connection": {"connectionReferenceLogicalName": "ini_firma_outlook"},
                                     "api": {"name": "shared_office365"}},
            },
            "definition": definicion,
            "templateName": "",
        },
        "schemaVersion": "1.0.0.0",
    }


if __name__ == "__main__":
    d = construir()
    DESTINO.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"escrito {DESTINO.name}: {len(d['properties']['definition']['actions'])} acciones de primer nivel")
