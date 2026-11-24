"""Genera flujos/FIRMA_F02_Aprobar_y_firmar.json: el flujo que llama al API de firma.

Se construye en Python en lugar de escribir el JSON a mano para que comparta, letra por letra, las
convenciones de F01/F03/F04 (Try/Catch, bitácora, correos con ModoPrueba, Marcar_accion_tomada).
Después: `python flujos/validar_parametros.py flujos/FIRMA_F02_Aprobar_y_firmar.json` y
`python flujos/subir_flujo.py --crear ... --si` (primera vez) o `--flujo ... --si --activar`.

Disparador: Accion = 'Aprobar', Accion_Fecha vacía, Estado 'Pendiente de firma' o 'Error de firma'
(reintento). Concurrencia 1 (irreversible una vez guardado, skill §2.12: decidido).

Varios documentos por solicitud (2026-11-24): las filas de FIRMA_Solicitud_Documentos en Pendiente o Error se
firman una a una (Scope por documento con su propio Catch: un documento que falla no tumba el bucle ni los ya
firmados). La solicitud pasa a Firmado solo si todos quedaron firmados; si no, a «Error de firma» con el detalle
de los que fallaron, y el reintento desde la app vuelve a firmar solo esos. El correo al solicitante ya no lo
manda F02: la app lo envía desde el buzón del gerente (Correo_Firmado_Pendiente) y F07 lo respalda a las 4 h.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
DESTINO = AQUI / "FIRMA_F02_Aprobar_y_firmar.json"
sys.path.insert(0, str(AQUI))
from comun import (  # noqa: E402
    APP_URL, CODIGO, EDITOR, ID, ID_FILTRO, LISTA_DOCUMENTOS, LISTA_PARAMETROS, LISTA_ROLES, MODO, PRUEBA, SITIO, SOPORTE,
    append_array, bitacora, compose, condicion, correo, correo_html, despues, fijar_variable, flujo, foreach,
    http_api, ok, parametro, patch_documento, patch_solicitud, query, scope, sp, trigger_sharepoint, variable_inicial,
)

DOC = "items('Cada_documento')"
DOC1 = "first(body('Documento_1')?['value'])"


def construir() -> dict:
    triggers = trigger_sharepoint(
        "@and(equals(triggerOutputs()?['body/Accion'],'Aprobar'), empty(triggerOutputs()?['body/Accion_Fecha']), "
        "or(equals(triggerOutputs()?['body/Estado/Value'],'Pendiente de firma'), equals(triggerOutputs()?['body/Estado/Value'],'Error de firma')))"
    )

    es_reintento = "equals(triggerOutputs()?['body/Estado/Value'],'Error de firma')"
    # Detalle de error de UN documento: el HTTP del mismo ciclo (si llegó a llamarse) y el resultado del Scope.
    detalle_doc = (
        "@concat('HTTP ', coalesce(string(actions('Llamar_api_firmar')?['outputs']?['statusCode']),'—'), ' ', "
        "coalesce(actions('Llamar_api_firmar')?['outputs']?['body']?['error'],''), ': ', "
        "coalesce(actions('Llamar_api_firmar')?['outputs']?['body']?['mensaje'],''), "
        "' · corr=', coalesce(actions('Llamar_api_firmar')?['outputs']?['body']?['correlation_id'],''), "
        "' · ', substring(string(result('Firmar_doc')), 0, min(length(string(result('Firmar_doc'))), 2500)))"
    )
    carpeta_html = "<p style=\"margin:12px 0 0\"><a href=\"@{triggerOutputs()?['body/Carpeta_Url']}\">Abrir la carpeta del documento en SharePoint</a></p>"
    enlace_app = (f"@{{if(or(empty({APP_URL}), equals({APP_URL},'SIN_CONFIGURAR')), '', "
                  f"concat('<p style=\"margin:16px 0 0\"><a href=\"', {APP_URL}, '\" style=\"background:#00539B;color:#fff;padding:10px 16px;"
                  f"text-decoration:none;border-radius:4px;display:inline-block\">Abrir la app INI Firma</a></p>'))}}")

    # ---------- un documento: Scope Firmar_doc + Catch_doc (dentro de Cada_documento) ----------
    firmar_doc = {
        # Url_Original es absoluta (sitio + /FIRMA_Documentos/...): la ruta relativa es lo que pide GetFileContentByPath.
        "Ruta_original": compose(f"@replace(coalesce({DOC}?['Url_Original'],''), {SITIO[1:]}, '')"),
        "Obtener_original": sp("GetFileContentByPath", {"dataset": SITIO, "path": "@outputs('Ruta_original')", "inferContentType": True},
                               ok("Ruta_original")),
        "Posicion_doc": compose({
            "pagina": f"@int(coalesce({DOC}?['Pagina_Firma'], triggerOutputs()?['body/Pagina_Firma'], 0))",
            "x_mm": "@float(coalesce(first(body('Param_X'))?['Valor'], '120'))",
            "y_mm": "@float(coalesce(first(body('Param_Y'))?['Valor'], '40'))",
            "ancho_mm": "@float(coalesce(first(body('Param_Ancho'))?['Valor'], '50'))",
            "ancla_texto": "@coalesce(first(body('Param_Ancla'))?['Valor'], '')",
            "ancla_alineacion": "centro",
            "si_no_hay_ancla": "posicion_fija",
            # Clic del gerente en la app sobre ESTE documento (centro de la firma, % del lienzo). null = sin clic.
            "x_pct": f"@{DOC}?['Firma_X_pct']",
            "y_pct": f"@{DOC}?['Firma_Y_pct']",
            "lienzo": f"@if(empty({DOC}?['Firma_Lienzo']), null, {DOC}?['Firma_Lienzo'])",
        }, ok("Obtener_original")),
        "Llamar_api_firmar": http_api("/firmar", {
            "solicitud_id": ID,
            "codigo": f"@{DOC}?['Codigo_Doc']",
            "pdf_base64": "@base64(body('Obtener_original'))",
            "firmante": {
                "nombre": "@coalesce(outputs('Firmante')?['Nombre_Firma'],'')",
                "cargo": "@coalesce(outputs('Firmante')?['Cargo_Firma'],'')",
                "email": "@toLower(coalesce(outputs('Firmante')?['Email'],''))",
                "imagen_id": "@outputs('Imagen_Id')",
                # Vacío si no hay adjunto: el API usa imagen_id. actions()?[...] no falla si la acción se omitió.
                "imagen_base64": "@coalesce(actions('Contenido_imagen')?['outputs']?['body']?['$content'], '')",
            },
            "fecha_firma": "@outputs('Fecha_firma_local')",
            "posicion": "@outputs('Posicion_doc')",
            "sello": {"texto": "@equals(toUpper(coalesce(first(body('Param_Sello'))?['Valor'],'NO')),'SI')", "codigo_verificacion": True},
        }, ok("Posicion_doc"),
            retry={"type": "exponential", "count": 4, "interval": "PT20S", "minimumInterval": "PT20S", "maximumInterval": "PT5M"},
            timeout="PT2M"),
        "Analizar_respuesta": {
            "type": "ParseJson",
            "inputs": {"content": "@body('Llamar_api_firmar')",
                       "schema": json.loads((AQUI.parent / "docs" / "esquema_respuesta_ok.json").read_text(encoding="utf-8"))},
            "runAfter": ok("Llamar_api_firmar"),
        },
        "Nombre_firmado": compose(f"@concat('Firmado_', formatNumber(int(coalesce({DOC}?['Orden'],1)),'00'), '_', coalesce({DOC}?['Title'],'documento.pdf'))",
                                  ok("Analizar_respuesta")),
        "Crear_firmado": sp("CreateFile", {
            "dataset": SITIO, "folderPath": "@outputs('Carpeta_relativa')", "name": "@outputs('Nombre_firmado')",
            "body": "@base64ToBinary(body('Analizar_respuesta')?['pdf_firmado_base64'])",
        }, ok("Nombre_firmado")),
        "Propiedades_firmado": sp("PatchFileItem", {
            "dataset": SITIO, "table": "@parameters('ini_FIRMA_BibliotecaId (ini_FIRMA_BibliotecaId)')",
            "id": "@body('Crear_firmado')?['ItemId']",
            "item/Solicitud_ID_Num": ID, "item/Codigo": CODIGO, "item/Tipo_Archivo/Value": "Firmado",
            "item/Orden_Anexo": f"@int(coalesce({DOC}?['Orden'],1))",
        }, ok("Crear_firmado")),
        "Actualizar_documento": patch_documento(f"@{DOC}?['ID']", {
            "item/Title": f"@{DOC}?['Title']",
            "item/Estado_Doc/Value": "Firmado",
            "item/Hash_Firmado": "@body('Analizar_respuesta')?['sha256']",
            "item/Codigo_Verificacion": "@body('Analizar_respuesta')?['codigo_verificacion']",
            "item/Url_Firmado": "@concat(triggerOutputs()?['body/Carpeta_Url'], '/', outputs('Nombre_firmado'))",
            "item/Bytes_Firmado": "@int(coalesce(body('Crear_firmado')?['Size'], 0))",
            "item/Fecha_Firma": "@utcNow()",
            "item/Error_Detalle": "",
        }, ok("Propiedades_firmado")),
        # El visor del documento pasa a mostrar el PDF firmado (mismo código → misma URL corta; la app añade ?v=).
        # En su propio Scope: si falla, el documento ya está firmado y la bitácora se escribe igual.
        "Visor_doc_firmado": scope({
            "Publicar_visor_doc": http_api("/visor", {
                "solicitud_id": ID, "codigo": f"@{DOC}?['Codigo_Doc']",
                "pdf_base64": "@body('Analizar_respuesta')?['pdf_firmado_base64']",
            }, retry={"type": "fixed", "count": 2, "interval": "PT20S"}),
        }, ok("Actualizar_documento")),
        "Bitacora_doc": bitacora(
            "Documento firmado", "@toLower(coalesce(outputs('Firmante')?['Email'], triggerOutputs()?['body/Editor/Email'], ''))",
            f"@concat({DOC}?['Codigo_Doc'], ' · ', outputs('Nombre_firmado'), ' · pág ', string(body('Analizar_respuesta')?['pagina_firmada']), '/', string(body('Analizar_respuesta')?['paginas']), "
            "' · ancla=', string(body('Analizar_respuesta')?['ancla_encontrada']), "
            "' · imagen=', if(greater(length(body('Imagenes_adjuntas')), 0), concat('adjunto:', last(body('Imagenes_adjuntas'))?['DisplayName']), outputs('Imagen_Id')), "
            "' · firma_mm=', string(body('Analizar_respuesta')?['firma_mm']), "
            f"' · clic=', string({DOC}?['Firma_X_pct']), '/', string({DOC}?['Firma_Y_pct']), "
            "' · sha256=', body('Analizar_respuesta')?['sha256'], "
            "' · verif=', body('Analizar_respuesta')?['codigo_verificacion'], ' · ', string(body('Analizar_respuesta')?['duracion_ms']), ' ms', "
            "' · visor=', if(equals(actions('Visor_doc_firmado')?['status'],'Succeeded'), 'ok', 'no disponible'))",
            despues("Visor_doc_firmado", "Succeeded", "Failed", "TimedOut"),
        ),
    }
    catch_doc = {
        "Detalle_doc": compose(detalle_doc),
        "Marcar_doc_error": patch_documento(f"@{DOC}?['ID']", {
            "item/Title": f"@{DOC}?['Title']", "item/Estado_Doc/Value": "Error", "item/Error_Detalle": "@outputs('Detalle_doc')",
        }, ok("Detalle_doc")),
        "Hay_error": fijar_variable("HayError", True, ok("Marcar_doc_error")),
        "Anotar_error": append_array("DocsError", f"@concat({DOC}?['Codigo_Doc'], ': ', outputs('Detalle_doc'))", ok("Hay_error")),
        "Bitacora_doc_error": bitacora("Error al firmar documento", f"@{EDITOR}",
                                       f"@concat({DOC}?['Codigo_Doc'], ' · ', outputs('Detalle_doc'))", ok("Anotar_error")),
    }

    # ---------- Try → rama válida ----------
    aprobar_then = {
        "Bitacora_aprobado": bitacora(
            "Aprobado", "@toLower(coalesce(outputs('Firmante')?['Email'], triggerOutputs()?['body/Editor/Email'], ''))",
            "@concat('Aprobado por ', outputs('Firmante')?['Title'], ' ', coalesce(outputs('Firmante')?['Nombre_Firma'],''), "
            "if(empty(triggerOutputs()?['body/Comentario_Gerente']), '', concat(' · comentario: ', triggerOutputs()?['body/Comentario_Gerente'])))",
        ),
        # Solo lo que falta por firmar (reintento desde «Error de firma»): filtro en positivo, `ne` no devuelve vacíos.
        "Documentos_pendientes": sp("GetItems", {
            "dataset": SITIO, "table": LISTA_DOCUMENTOS,
            "$filter": f"Solicitud_ID_Num eq {ID_FILTRO} and (Estado_Doc eq 'Pendiente' or Estado_Doc eq 'Error')",
            "$orderby": "Orden asc", "$top": 50,
        }, ok("Bitacora_aprobado")),
        "Fecha_firma_local": compose("@convertTimeZone(utcNow(),'UTC','SA Pacific Standard Time','yyyy-MM-ddTHH:mm:ss')", ok("Documentos_pendientes")),
        "Cada_documento": foreach("@body('Documentos_pendientes')?['value']", {
            "Firmar_doc": scope(firmar_doc),
            "Catch_doc": scope(catch_doc, despues("Firmar_doc", "Failed", "TimedOut")),
            # Cierra la iteración en Succeeded aunque el documento haya fallado (el error ya quedó en la fila y en HayError).
            "Fin_doc": compose("ok", despues("Catch_doc", "Succeeded", "Skipped")),
        }, ok("Fecha_firma_local")),
        "Documento_1": sp("GetItems", {"dataset": SITIO, "table": LISTA_DOCUMENTOS,
                                       "$filter": f"Solicitud_ID_Num eq {ID_FILTRO} and Orden eq 1", "$top": 1}, ok("Cada_documento")),
        "Resultado": condicion({"equals": ["@variables('HayError')", False]}, {
            "Pasar_a_firmado": patch_solicitud({
                "item/Estado/Value": "Firmado",
                "item/Fecha_Firma": "@utcNow()",
                "item/Firmado_Por_Email": "@toLower(coalesce(outputs('Firmante')?['Email'], triggerOutputs()?['body/Editor/Email'], ''))",
                "item/Firmado_Por_Nombre": "@coalesce(outputs('Firmante')?['Nombre_Firma'], triggerOutputs()?['body/Editor/DisplayName'], '')",
                # Campos antiguos (un documento) = documento 01, para la bandeja, F05 y las solicitudes ya cerradas.
                "item/Hash_Firmado": f"@coalesce({DOC1}?['Hash_Firmado'], '')",
                "item/Codigo_Verificacion": f"@coalesce({DOC1}?['Codigo_Verificacion'], '')",
                "item/Url_Firmado": f"@coalesce({DOC1}?['Url_Firmado'], '')",
                # Url_Visor se conserva: el visor del documento 01 sirve ya el PDF firmado.
                "item/Error_Detalle": "",
                # El correo al solicitante lo manda la app desde el buzón del gerente; F07 lo respalda a las 4 h.
                "item/Correo_Firmado_Pendiente": True,
                "item/Correo_Firmado_Enviado_Por": "",
            }),
            "Bitacora_firmado": bitacora(
                "Firmado", "@toLower(coalesce(outputs('Firmante')?['Email'], triggerOutputs()?['body/Editor/Email'], ''))",
                "@concat(string(length(body('Documentos_pendientes')?['value'])), ' documento(s) firmado(s) en esta ejecución · correo al solicitante pendiente (lo envía el gerente desde la app)')",
                ok("Pasar_a_firmado"),
            ),
            "Correo_firmante": correo(
                to=f"@{{if(equals({MODO},'SI'), {SOPORTE}, coalesce(triggerOutputs()?['body/Editor/Email'], {SOPORTE}))}}",
                subject=f"@{{{PRUEBA}}}Firma registrada: @{{triggerOutputs()?['body/Title']}} (@{{triggerOutputs()?['body/Codigo']}})",
                body=correo_html(
                    "Firma registrada",
                    "Los documentos quedaron firmados. El aviso al solicitante sale de tu buzón al abrir la app INI Firma (si no lo haces en 4 horas, lo envía el sistema).",
                    [("Código", "@{triggerOutputs()?['body/Codigo']}"),
                     ("Título", "@{triggerOutputs()?['body/Title']}"),
                     ("Documentos firmados", "@{length(body('Documentos_pendientes')?['value'])}"),
                     ("Solicitante", "@{coalesce(triggerOutputs()?['body/Solicitante_Nombre'], triggerOutputs()?['body/Solicitante/DisplayName'], triggerOutputs()?['body/Author/DisplayName'])}"),
                     ("Código de verificación (doc. 1)", f"@{{coalesce({DOC1}?['Codigo_Verificacion'], '—')}}")],
                    carpeta_html + "\n" + enlace_app,
                ),
                run_after=ok("Bitacora_firmado"),
            ),
        }, {
            "Marcar_error_docs": patch_solicitud({
                "item/Estado/Value": "Error de firma", "item/Accion": "Error",
                "item/Error_Detalle": "@substring(join(variables('DocsError'), ' | '), 0, min(length(join(variables('DocsError'), ' | ')), 3500))",
            }),
            "Bitacora_error_docs": bitacora("Error en F02 Firmar", f"@{EDITOR}", "@join(variables('DocsError'), ' | ')", ok("Marcar_error_docs")),
            "Correo_soporte_docs": correo(
                to=f"@{{{SOPORTE}}}",
                subject="[FIRMA] Error al firmar documentos · solicitud @{triggerOutputs()?['body/ID']}",
                body=correo_html(
                    "Error al firmar uno o más documentos",
                    "La solicitud queda en «Error de firma». Los documentos ya firmados se conservan; al reintentar desde la app solo se firman los que fallaron.",
                    [("Solicitud", "@{triggerOutputs()?['body/ID']} · @{triggerOutputs()?['body/Title']}"),
                     ("Código", "@{triggerOutputs()?['body/Codigo']}"),
                     ("Ejecución", "@{workflow()?['run']?['name']}"),
                     ("Detalle", "@{join(variables('DocsError'), '<br>')}")],
                ),
                importance="High",
                run_after=ok("Bitacora_error_docs"),
            ),
        }, ok("Documento_1")),
    }

    aprobar_else = {
        "Motivo_invalido": compose(
            "@if(equals(length(body('Firmantes_autorizados')),0), "
            f"if(and({es_reintento}, greater(length(body('Administradores')),0)), '', "
            f"concat('Acción no autorizada: ', {EDITOR}, ' no es gerente ni suplente activo.')), "
            "if(empty(outputs('Imagen_Id')), 'El firmante no tiene imagen de firma: debe subirla en la app (Mi firma) o fijar Imagen_Firma_Id en FIRMA_Roles.', "
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
        # La imagen que el gerente/suplente sube en la app («Mi firma») es un adjunto de su fila en FIRMA_Roles y manda
        # sobre Imagen_Firma_Id (origen fijo del API). Se toma la última imagen PNG/JPG adjunta.
        "Adjuntos_firmante": sp("GetItemAttachments", {
            "dataset": SITIO, "table": LISTA_ROLES, "itemId": "@outputs('Firmante')?['ID']",
        }, ok("Firmante")),
        "Imagenes_adjuntas": query(
            "@body('Adjuntos_firmante')",
            "@or(endsWith(toLower(coalesce(item()?['DisplayName'], item()?['Name'], '')), '.png'), "
            "endsWith(toLower(coalesce(item()?['DisplayName'], item()?['Name'], '')), '.jpg'), "
            "endsWith(toLower(coalesce(item()?['DisplayName'], item()?['Name'], '')), '.jpeg'))",
            ok("Adjuntos_firmante"),
        ),
        "Leer_imagen_adjunta": condicion({"greater": ["@length(body('Imagenes_adjuntas'))", 0]}, {
            "Contenido_imagen": sp("GetAttachmentContent", {
                "dataset": SITIO, "table": LISTA_ROLES, "itemId": "@outputs('Firmante')?['ID']",
                "attachmentId": "@last(body('Imagenes_adjuntas'))?['Id']",
            }),
        }, run_after=ok("Imagenes_adjuntas")),
        "Carpeta_relativa": compose(
            f"@replace(coalesce(triggerOutputs()?['body/Carpeta_Url'],''), {SITIO[1:]}, '')",
            ok("Firmante"),
        ),
        "Param_X": parametro("Firma_X_mm"),
        "Param_Y": parametro("Firma_Y_mm"),
        "Param_Ancho": parametro("Firma_Ancho_mm"),
        "Param_Ancla": parametro("Firma_Ancla_Texto"),
        "Param_Sello": parametro("Texto_Sello"),
        "Validar_aprobacion": condicion({"and": [
            {"or": [
                {"greater": ["@length(body('Firmantes_autorizados'))", 0]},
                {"and": [{"equals": ["@triggerOutputs()?['body/Estado/Value']", "Error de firma"]},
                         {"greater": ["@length(body('Administradores'))", 0]}]},
            ]},
            {"or": [
                {"not": {"equals": ["@outputs('Imagen_Id')", ""]}},
                {"greater": ["@length(body('Imagenes_adjuntas'))", 0]},
            ]},
            {"not": {"equals": ["@outputs('Carpeta_relativa')", "@coalesce(triggerOutputs()?['body/Carpeta_Url'],'')"]}},
        ]}, aprobar_then, aprobar_else,
            ok("Imagen_Id", "Leer_imagen_adjunta", "Carpeta_relativa", "Param_X", "Param_Y", "Param_Ancho", "Param_Ancla", "Param_Sello")),
    }

    # El Catch exterior ya no puede mirar el HTTP (vive dentro del bucle): queda el resultado del Try.
    catch_actions = {
        "Detalle_error": compose("@substring(string(result('Try')), 0, min(length(string(result('Try'))), 3000))"),
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
        # Variables solo en la raíz (InitializeVariable no se admite dentro de Scope/Foreach).
        "Iniciar_HayError": variable_inicial("HayError", "boolean", False, ok("Marcar_accion_tomada")),
        "Iniciar_DocsError": variable_inicial("DocsError", "array", [], ok("Iniciar_HayError")),
        "Leer_roles": sp("GetItems", {"dataset": SITIO, "table": LISTA_ROLES, "$filter": "Activo eq 1", "$orderby": "Title", "$top": 50},
                         ok("Iniciar_DocsError")),
        "Leer_parametros": sp("GetItems", {"dataset": SITIO, "table": LISTA_PARAMETROS, "$orderby": "Clave", "$top": 50},
                              ok("Iniciar_DocsError")),
        "Filtrar_Gerente": query("@body('Leer_roles')?['value']", "@equals(item()?['Title'],'Gerente')", ok("Leer_roles")),
        "Filtrar_Secretaria": query("@body('Leer_roles')?['value']", "@equals(item()?['Title'],'Secretaria')", ok("Leer_roles")),
        "Try": scope(try_actions, ok("Filtrar_Gerente", "Filtrar_Secretaria", "Leer_parametros")),
        "Catch": scope(catch_actions, despues("Try", "Failed", "TimedOut")),
    }
    return flujo("FIRMA F02 · Aprobar y firmar", triggers, actions)


if __name__ == "__main__":
    d = construir()
    DESTINO.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"escrito {DESTINO.name}: {len(d['properties']['definition']['actions'])} acciones de primer nivel")
