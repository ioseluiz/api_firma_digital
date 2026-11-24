"""Genera flujos/FIRMA_F01_Registrar_envio.json a partir de la línea base (flujos/base/FIRMA_F01_base_2026-11-24.json,
la definición de cpitti con los ajustes del visor) aplicando la versión «varios documentos por solicitud».

Por qué transformar la base en vez de reescribir: el F01 original tiene correos, validaciones y Catch que no
cambian; tocar solo lo necesario (skill §2.2) mantiene el diff legible y evita regresiones.

Cambios respecto a la base (2026-11-24):
- Se admiten varios documentos a firmar: para Origen = SharePoint, todos los MEMO_*.pdf (uno o más); para
  Origen = App (pantalla de la secretaria, que no puede renombrar adjuntos), todo PDF que no empiece por ANEXO_.
- Bucle Cada_documento (concurrencia 1): Original_<nn>_<nombre>.pdf, visor por documento (código <codigo>-<nn>)
  y una fila en FIRMA_Solicitud_Documentos. Antes se borran filas hijas previas (reintentos idempotentes).
- Los campos antiguos de la solicitud (Url_Original, Url_Visor) se rellenan con el documento 01, leído de la
  lista hija DESPUÉS del bucle (actions() de una acción del bucle no vale fuera de él).
- Correo al gerente solo si la app no lo envió ya (Correo_Gerente_Enviado), con ReplyTo = quien registró.

Después: `python flujos/validar_parametros.py flujos/FIRMA_F01_Registrar_envio.json` y
`python flujos/subir_flujo.py --flujo flujos/FIRMA_F01_Registrar_envio.json --si --activar`.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
from comun import (  # noqa: E402
    BIBLIOTECA, ID, ID_FILTRO, LISTA_DOCUMENTOS, SITIO, compose, condicion, despues, foreach, http_api, incrementar, ok, scope, sp,
    variable_inicial,
)

BASE = AQUI / "base" / "FIRMA_F01_base_2026-11-24.json"
DESTINO = AQUI / "FIRMA_F01_Registrar_envio.json"

NN = "formatNumber(variables('OrdenDoc'),'00')"
CODIGO_DOC = f"@concat(outputs('Codigo'),'-',{NN})"
ORIGEN_APP = "equals(coalesce(triggerOutputs()?['body/Origen/Value'],'SharePoint'),'App')"
GERENTE_AVISADO = "equals(coalesce(triggerOutputs()?['body/Correo_Gerente_Enviado'],false),true)"


def construir() -> dict:
    d = json.loads(BASE.read_text(encoding="utf-8"))
    df = d["properties"]["definition"]
    acts = df["actions"]
    tr = acts["Try"]["actions"]
    va = tr["Validar_adjuntos"]
    then = va["actions"]
    sino = va["else"]["actions"]

    # ---- raíz: contador de documentos
    acts["Iniciar_orden_doc"] = variable_inicial("OrdenDoc", "integer", 0, ok("Iniciar_orden"))
    assert acts["Marcar_accion_tomada"]["runAfter"] == ok("Iniciar_orden")
    acts["Marcar_accion_tomada"]["runAfter"] = ok("Iniciar_orden_doc")

    # ---- qué se firma: MEMO_ (SharePoint) o todo PDF salvo ANEXO_ (App)
    tr["Filtrar_memo"]["inputs"]["where"] = (
        f"@if({ORIGEN_APP}, and(endsWith(toLower(item()?['DisplayName']),'.pdf'), not(startsWith(toLower(item()?['DisplayName']),'anexo_'))), "
        "startsWith(toLower(item()?['DisplayName']),'memo_'))"
    )
    cond = va["expression"]["and"]
    assert cond[0] == {"equals": ["@length(body('Filtrar_memo'))", 1]}
    cond[0] = {"greater": ["@length(body('Filtrar_memo'))", 0]}
    viejo = "if(equals(length(body('Filtrar_memo')),1),'',concat('Debe haber exactamente un archivo MEMO_....pdf (hay ', length(body('Filtrar_memo')), '). '))"
    motivo = sino["Motivo_invalido"]["inputs"]
    assert viejo in motivo
    sino["Motivo_invalido"]["inputs"] = motivo.replace(
        viejo, "if(greater(length(body('Filtrar_memo')),0),'','Debe haber al menos un archivo PDF a firmar (MEMO_....pdf). ')")
    # Si la app ya avisó al gerente, el correo de «no se pudo enviar» le llega también a él para que ignore el aviso.
    sino["Correo_invalido"]["inputs"]["parameters"]["emailMessage/Cc"] = (
        f"@{{if({GERENTE_AVISADO}, coalesce(first(body('Filtrar_Gerente'))?['Email'],''), '')}}"
    )

    # ---- un documento → N documentos
    for nombre in ("Contenido_memo", "Crear_original", "Propiedades_original", "Publicar_visor"):
        then.pop(nombre)
    filtro_hija = f"Solicitud_ID_Num eq {ID_FILTRO}"
    then["Filas_previas"] = sp("GetItems", {"dataset": SITIO, "table": LISTA_DOCUMENTOS, "$filter": filtro_hija, "$top": 50}, ok("Crear_carpeta"))
    then["Quitar_filas_previas"] = foreach("@body('Filas_previas')?['value']", {
        "Borrar_fila": sp("DeleteItem", {"dataset": SITIO, "table": LISTA_DOCUMENTOS, "id": "@items('Quitar_filas_previas')?['ID']"}),
    }, ok("Filas_previas"))
    then["Cada_documento"] = foreach("@sort(body('Filtrar_memo'),'DisplayName')", {
        "Sumar_orden_doc": incrementar("OrdenDoc"),
        "Doc": scope({
            "Contenido_doc": sp("GetAttachmentContent", {
                "dataset": SITIO, "table": "767347d5-3d27-4b12-8360-f114d5c51002", "itemId": ID,
                "attachmentId": "@items('Cada_documento')?['Id']",
            }),
            "Nombre_doc": compose(f"@concat('Original_', {NN}, '_', items('Cada_documento')?['DisplayName'])", ok("Contenido_doc")),
            "Crear_doc": sp("CreateFile", {
                "dataset": SITIO, "folderPath": "@concat('/FIRMA_Documentos/', outputs('Carpeta'))",
                "name": "@outputs('Nombre_doc')", "body": "@body('Contenido_doc')",
            }, ok("Nombre_doc")),
            "Propiedades_doc": sp("PatchFileItem", {
                "dataset": SITIO, "table": BIBLIOTECA, "id": "@body('Crear_doc')?['ItemId']",
                "item/Solicitud_ID_Num": ID, "item/Codigo": "@outputs('Codigo')",
                "item/Tipo_Archivo/Value": "Original", "item/Orden_Anexo": "@variables('OrdenDoc')",
            }, ok("Crear_doc")),
            # El visor en su propio Scope: si el API falla, la fila se crea igual (sin visor) y el envío no se bloquea.
            "Visor_doc": scope({
                "Publicar_visor_doc": http_api("/visor", {
                    "solicitud_id": ID, "codigo": CODIGO_DOC, "pdf_base64": "@base64(body('Contenido_doc'))",
                }),
            }, ok("Propiedades_doc")),
            "Fila_doc": sp("PostItem", {
                "dataset": SITIO, "table": LISTA_DOCUMENTOS,
                "item/Title": "@items('Cada_documento')?['DisplayName']",
                "item/Solicitud_ID_Num": ID, "item/Codigo": "@outputs('Codigo')",
                "item/Orden": "@variables('OrdenDoc')", "item/Codigo_Doc": CODIGO_DOC,
                "item/Url_Original": f"@concat({SITIO[1:]}, '/FIRMA_Documentos/', outputs('Carpeta'), '/', outputs('Nombre_doc'))",
                "item/Url_Visor": "@coalesce(actions('Publicar_visor_doc')?['outputs']?['body']?['url_visor'], '')",
                "item/Pagina_Firma": "@int(coalesce(triggerOutputs()?['body/Pagina_Firma'], 0))",
                "item/Estado_Doc/Value": "Pendiente",
            }, despues("Visor_doc", "Succeeded", "Failed", "TimedOut")),
        }, ok("Sumar_orden_doc")),
    }, ok("Quitar_filas_previas"))
    then["Cada_anexo"]["runAfter"] = ok("Cada_documento")

    # ---- documento 01 para los campos antiguos de la solicitud
    then["Documento_1"] = sp("GetItems", {"dataset": SITIO, "table": LISTA_DOCUMENTOS,
                                          "$filter": f"{filtro_hija} and Orden eq 1", "$top": 1}, ok("Quitar_adjuntos"))
    pp = then["Pasar_a_pendiente"]
    pp["runAfter"] = ok("Documento_1")
    p = pp["inputs"]["parameters"]
    p["item/Url_Original"] = "@coalesce(first(body('Documento_1')?['value'])?['Url_Original'], '')"
    p["item/Url_Visor"] = "@coalesce(first(body('Documento_1')?['value'])?['Url_Visor'], '')"
    p["item/Cantidad_Documentos"] = "@length(body('Filtrar_memo'))"
    b = then["Bitacora_enviado"]["inputs"]["parameters"]
    b["item/Detalle"] = ("@{concat(length(body('Filtrar_memo')), ' documento(s) y ', length(body('Filtrar_anexos')), ' anexo(s) movidos a ', "
                         "outputs('Carpeta'), ' · visor=', if(empty(coalesce(first(body('Documento_1')?['value'])?['Url_Visor'],'')), 'no disponible', 'ok'))}")

    # ---- aviso al gerente: solo si la app no lo mandó ya; responder-a = quien registró
    cg = then.pop("Correo_gerente")
    cg["runAfter"] = {}
    cp = cg["inputs"]["parameters"]
    cp["emailMessage/ReplyTo"] = "@{coalesce(triggerOutputs()?['body/Registrado_Por_Email'], triggerOutputs()?['body/Author/Email'])}"
    cuerpo = cp["emailMessage/Body"]
    m = re.search(r"<tr>(?:(?!</tr>).)*?>Anexos</td>.*?</tr>", cuerpo, re.S)
    assert m, "no encuentro la fila Anexos del correo al gerente"
    fila_docs = ('<tr><td style="padding:6px 8px;background:#f3f6f9;width:170px;font-weight:600">Documentos a firmar</td>'
                 '<td style="padding:6px 8px">@{length(body(\'Filtrar_memo\'))}</td></tr>')
    cp["emailMessage/Body"] = cuerpo[:m.start()] + fila_docs + cuerpo[m.start():]
    then["Avisar_gerente"] = condicion({"not": {"equals": [f"@{GERENTE_AVISADO}", True]}}, {"Correo_gerente": cg},
                                       run_after=ok("Bitacora_enviado"))
    assert then["Correo_solicitante"]["runAfter"] == ok("Correo_gerente")
    then["Correo_solicitante"]["runAfter"] = ok("Avisar_gerente")
    return d


if __name__ == "__main__":
    d = construir()
    DESTINO.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    then = d["properties"]["definition"]["actions"]["Try"]["actions"]["Validar_adjuntos"]["actions"]
    print(f"escrito {DESTINO.name}: rama válida = {list(then)}")
