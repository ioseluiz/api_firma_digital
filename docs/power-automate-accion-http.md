# Llamar al API de firma desde Power Automate (flujos F01 y F02)

Configuración exacta de las acciones que hablan con el API. **El flujo F02 real está como código en
`../flujos/FIRMA_F02_Aprobar_y_firmar.json` (generado por `flujos/generar_f02.py`)**; esta página explica
las decisiones y sirve para leerlo. Para el resto (disparador, verificación del `Editor`, correos) ver
`../../PLAN.md` §7.3 y `../flujos/README.md`.

Requisitos: licencia Power Automate Premium para el dueño del flujo (la acción HTTP es premium) y
que la política DLP del ambiente permita el conector HTTP junto con SharePoint.

## Variables de entorno de la solución

| Nombre | Tipo | Valor |
|---|---|---|
| `ini_FIRMA_ApiUrl` | Texto | **Base** `https://func-ini-firma-dev.azurewebsites.net/api/v1` (F01 llama `…/visor`, F02 `…/firmar` y `…/visor/{codigo}`) |
| `ini_FIRMA_ApiKey` | Texto | clave de función **dedicada** `powerautomate` (`az functionapp keys set … --key-name powerautomate`; se fija con `flujos/variables_entorno.py --fijar ini_FIRMA_ApiKey --desde-az`). El tipo Secreto exigiría Key Vault. |
| `ini_FIRMA_AppUrl` | Texto | URL de reproducción de la app INI Firma; `SIN_CONFIGURAR` hasta tenerla. Los correos enlazan a ella. |

La imagen de firma sale de `FIRMA_Roles.Imagen_Firma_Id` del firmante (Gerente o Suplente), con
`gerente.png` como respaldo para el Gerente.

## 1. Obtener el PDF original

Acción **SharePoint → Obtener contenido del archivo** (`Get file content`) con el identificador del
archivo `Original.pdf` de la carpeta de la solicitud. Para el cuerpo de la petición se usa
`base64(body('Obtener_contenido_del_archivo'))`, que es la forma documentada de convertir el
contenido binario de esa acción a base64. (Su salida cruda es
`{"$content-type": "application/pdf", "$content": "<base64>"}`, así que `?['$content']` también
funciona, pero `base64()` no depende de ese formato interno.) El API (desde 0.2.0) tolera
que `base64()` produzca base64 doble o el base64 de ese objeto, cosa que ocurre con el
`contentBytes` del desencadenador manual; lo que sí rompe el flujo es interpolar el binario sin
`base64()`, porque llega `%PDF` decodificado con los bytes rotos.

Antes de integrar, conviene probar el API con un flujo aislado: `prueba-power-automate.md`.

## 2. Acción HTTP

| Campo | Valor |
|---|---|
| Método | `POST` |
| URI | `@{parameters('ini_FIRMA_ApiUrl (ini_FIRMA_ApiUrl)')}/firmar` |
| Encabezados | `Content-Type`: `application/json` · `x-functions-key`: `@{parameters('ini_FIRMA_ApiKey (ini_FIRMA_ApiKey)')}` · `X-Correlation-Id`: `@{workflow()?['run']?['name']}` |
| Autenticación | Ninguna (la clave va en la cabecera) |
| Configuración → Tiempo de espera | `PT2M` |
| Configuración → Directiva de reintentos | Exponencial, 4 reintentos, intervalo `PT20S` |

Cuerpo (mínimo, solo la imagen en la última página):

```json
{
  "solicitud_id": @{triggerOutputs()?['body/ID']},
  "codigo": "@{triggerOutputs()?['body/Codigo']}",
  "pdf_base64": "@{base64(body('Obtener_contenido_del_archivo'))}",
  "firmante": { "imagen_id": "@{parameters('ini_FIRMA_ImagenId')}" },
  "fecha_firma": "@{convertTimeZone(utcNow(), 'UTC', 'SA Pacific Standard Time', 'yyyy-MM-ddTHH:mm:ss')}"
}
```

Cuerpo con posición por ancla (la firma se centra sobre el texto indicado, en la última página):

```json
{
  "solicitud_id": @{triggerOutputs()?['body/ID']},
  "codigo": "@{triggerOutputs()?['body/Codigo']}",
  "pdf_base64": "@{base64(body('Obtener_contenido_del_archivo'))}",
  "firmante": { "imagen_id": "@{parameters('ini_FIRMA_ImagenId')}" },
  "fecha_firma": "@{convertTimeZone(utcNow(), 'UTC', 'SA Pacific Standard Time', 'yyyy-MM-ddTHH:mm:ss')}",
  "posicion": {
    "pagina": @{coalesce(triggerOutputs()?['body/Pagina_Firma'], 0)},
    "ancla_texto": "@{coalesce(triggerOutputs()?['body/Ancla_Texto'], '')}",
    "ancla_alineacion": "centro",
    "x_mm": 120, "y_mm": 40, "ancho_mm": 40,
    "si_no_hay_ancla": "posicion_fija"
  }
}
```

Notas:

- El nombre de la acción en las expresiones (`Obtener_contenido_del_archivo`) es el que tenga en el
  diseñador, con espacios reemplazados por `_`.
- `SA Pacific Standard Time` es la zona de Panamá (UTC−5, sin horario de verano).
- Los nombres de columna (`Codigo`, `Pagina_Firma`, `Ancla_Texto`) son los **internos** de
  SharePoint, no los de mostrar. Comprobarlos en `_api/web/lists/getbytitle('FIRMA_Solicitudes')/fields`.
- Límite práctico de 20 MB por PDF validado en F01; el API rechaza más de 25 MB con 413.

## 3. Analizar JSON (respuesta correcta)

Acción **Analizar JSON** con *Contenido* = `@{body('HTTP')}` y este esquema
(generado desde el modelo del API; también en `esquema_respuesta_ok.json`):

```json
{
  "type": "object",
  "properties": {
    "ok": { "type": "boolean" },
    "codigo": { "type": "string" },
    "pdf_firmado_base64": { "type": "string" },
    "sha256": { "type": "string" },
    "codigo_verificacion": { "type": "string" },
    "paginas": { "type": "integer" },
    "pagina_firmada": { "type": "integer" },
    "ancla_encontrada": { "type": ["boolean", "null"] },
    "firmante": { "type": "string" },
    "fecha_firma": { "type": "string" },
    "duracion_ms": { "type": "integer" }
  }
}
```

## 4. Guardar el PDF firmado

Acción **SharePoint → Crear archivo** (`Create file`):

| Campo | Valor |
|---|---|
| Dirección del sitio | sitio INI |
| Ruta de la carpeta | `@{triggerOutputs()?['body/Carpeta_Url']}` (relativa al sitio, p. ej. `/FIRMA_Documentos/2026/FIRMA-2026-000123 - Memorando`) |
| Nombre del archivo | `Firmado.pdf` |
| Contenido del archivo | `@{base64ToBinary(body('Analizar_JSON')?['pdf_firmado_base64'])}` |

Después, **Actualizar elemento** en `FIRMA_Solicitudes`: `Estado = Firmado`, `Fecha_Firma`,
`Hash_Firmado = @{body('Analizar_JSON')?['sha256']}`,
`Codigo_Verificacion = @{body('Analizar_JSON')?['codigo_verificacion']}`, `Url_Firmado`.

## 5. Errores

El API responde siempre JSON; en error, HTTP 4xx/5xx con este cuerpo (`esquema_respuesta_error.json`):

```json
{ "ok": false, "error": "pdf_invalido", "mensaje": "texto para humanos", "correlation_id": "..." }
```

| HTTP | `error` | Qué hacer en el flujo |
|---|---|---|
| 400 | `peticion_invalida`, `pdf_invalido`, `pagina_fuera_de_rango` | No reintentar. `Estado = Error de firma`, `Error_Detalle = mensaje`, correo a TI. Si es `pdf_invalido` por cifrado, el mensaje ya lo explica: puede ir al solicitante. |
| 401 | — | Clave de función incorrecta o vencida. Correo a TI. |
| 404 | `imagen_no_encontrada` | Configuración (`imagen_id`). Correo a TI. |
| 413 | `pdf_demasiado_grande` | F01 debió impedirlo; correo al solicitante. |
| 422 | `ancla_no_encontrada` | Solo si se mandó `si_no_hay_ancla: "error"`. Reintentar sin ancla o avisar. |
| 500 | `error_interno` | La directiva de reintentos ya lo intentó 4 veces. `Error de firma`, correo a TI con `correlation_id`. |

Estructura recomendada: la acción HTTP, Analizar JSON, Crear archivo y Actualizar elemento dentro de
un ámbito **Try**; un ámbito **Catch** con *Ejecutar después* = «ha fallado, se ha omitido, ha
agotado el tiempo» que lee `outputs('HTTP')?['statusCode']` y `body('HTTP')?['mensaje']`
(ambos pueden ser null si no hubo respuesta), escribe `Estado = Error de firma` y avisa a TI.
Al solicitante nunca se le avisa desde el Catch: para él la solicitud sigue pendiente.

## 6. Prueba rápida sin flujo

Desde Postman con la colección de `../postman/` y un entorno generado con
`--base-url https://func-ini-firma-dev.azurewebsites.net --clave <clave> --nombre dev`. Si la
colección pasa, el flujo solo tiene que reproducir la petición 02.

## Visor en F01 (`Publicar_visor`)

Tras crear `Original.pdf`, F01 llama `POST @{ApiUrl}/visor` con `{"solicitud_id", "codigo",
"pdf_base64": base64(body('Contenido_memo'))}` y guarda `body.url_visor` en `Url_Visor` de la
solicitud. La acción corre con 2 reintentos y 1 minuto de espera, y **`Cada_anexo` continúa aunque
falle** (`runAfter: Succeeded, Failed, TimedOut`): sin visor la app muestra «Abrir en SharePoint».
La bitácora anota `visor=ok` o `visor=no disponible`. F02 borra el blob con `DELETE @{ApiUrl}/visor/{codigo}`
después de firmar, también sin bloquear la ejecución si falla. La URL SAS vale 7 días como máximo
(clave de delegación de usuario); ver README del API.
