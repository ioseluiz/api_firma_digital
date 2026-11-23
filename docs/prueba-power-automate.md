# Flujo de prueba: firmar un PDF desde Power Automate con la acción HTTP

Flujo aislado, sin SharePoint ni listas, para comprobar que Power Automate llega al API en Azure.
Un botón manual recibe un PDF, lo envía a `func-ini-firma-dev` y guarda el firmado en OneDrive.
Tiempo de armado: unos 10 minutos.

Antes de empezar, obtener la clave de función (no la pegues en correos ni chats):

```powershell
& $env:USERPROFILE\.venv-azcli\Scripts\az.bat functionapp keys list -g rg-ini-firma-dev -n func-ini-firma-dev --query functionKeys.default -o tsv
```

## 1. Crear el flujo

make.powerautomate.com → **Crear** → **Flujo de nube instantáneo** → nombre `Prueba API Firma` →
desencadenador **Desencadenar un flujo manualmente** → Crear.

En el desencadenador, **Agregar una entrada** → **Archivo** → nombre `Documento`.
(Opcional: otra entrada de tipo **Texto** llamada `Ancla`, para probar el posicionamiento por texto.)

## 2. Acción «Redactar» (Compose) — `fecha_firma`

Nombre: `Fecha firma`. Entradas (expresión):

```
convertTimeZone(utcNow(), 'UTC', 'SA Pacific Standard Time', 'yyyy-MM-ddTHH:mm:ss')
```

## 3. Acción «HTTP»

| Campo | Valor |
|---|---|
| Método | `POST` |
| URI | `https://func-ini-firma-dev.azurewebsites.net/api/v1/firmar` |
| Encabezados | `Content-Type` → `application/json` · `x-functions-key` → *la clave* · `X-Correlation-Id` → expresión `workflow()?['run']?['name']` |
| Cuerpo | el JSON de abajo |

Cuerpo. Los valores entre `@{...}` se ponen como **expresión** (pestaña *fx*), no como texto:

```json
{
  "solicitud_id": 1,
  "codigo": "PRUEBA-PA-001",
  "pdf_base64": "@{triggerBody()?['Documento']?['contentBytes']}",
  "firmante": { "imagen_id": "gerente.png" },
  "fecha_firma": "@{outputs('Fecha_firma')}",
  "posicion": {
    "pagina": 0,
    "ancla_texto": "@{coalesce(triggerBody()?['Ancla'], '')}",
    "ancla_alineacion": "centro",
    "x_mm": 120, "y_mm": 40, "ancho_mm": 40,
    "si_no_hay_ancla": "posicion_fija"
  }
}
```

Notas:

- `contentBytes` de una entrada de tipo Archivo **ya viene en base64**: se manda tal cual. Si en
  lugar del desencadenador el PDF viniera de «Obtener contenido del archivo» (OneDrive o
  SharePoint), la expresión sería `base64(body('Obtener_contenido_del_archivo'))`.
- Si no creaste la entrada `Ancla`, borra la línea de `ancla_texto` o deja `""`.
- En **Configuración** de la acción HTTP (menú `…`): tiempo de espera `PT2M`; directiva de
  reintentos *exponencial*, 4 intentos, intervalo `PT20S`. En la prueba se puede dejar por defecto.

## 4. Acción «Analizar JSON»

Contenido: `body('HTTP')` (contenido dinámico *Cuerpo* de HTTP). Esquema:

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

## 5. Acción «OneDrive para la Empresa → Crear archivo»

| Campo | Valor |
|---|---|
| Ruta de la carpeta | `/Firmas-prueba` (créala antes en OneDrive, o elígela con el selector) |
| Nombre del archivo | expresión `replace(triggerBody()?['Documento']?['name'], '.pdf', '_firmado.pdf')` |
| Contenido del archivo | expresión `base64ToBinary(body('Analizar_JSON')?['pdf_firmado_base64'])` |

## 6. (Opcional) Acción «Redactar» — resumen

Para ver en el historial qué devolvió el API sin abrir el PDF:

```
concat('Página ', body('Analizar_JSON')?['pagina_firmada'], ' de ', body('Analizar_JSON')?['paginas'],
       ' · ancla=', string(body('Analizar_JSON')?['ancla_encontrada']),
       ' · sha256=', body('Analizar_JSON')?['sha256'],
       ' · ', body('Analizar_JSON')?['duracion_ms'], ' ms')
```

## 7. Guardar y probar

**Guardar**. Si al guardar aparece un error de **DLP** («el conector HTTP no se puede usar junto
con…»), la política del ambiente bloquea HTTP: anotar el mensaje exacto y pedir al administrador de
Power Platform un ambiente donde esté permitido. Es el único punto que depende de TI.

**Probar** → *Manualmente* → elegir un PDF (por ejemplo `salida/CronogramaPedroMiguelAF27.pdf`;
con ancla `Gerente INIG`) → Ejecutar flujo. En el historial, cada acción muestra entradas y salidas;
la acción HTTP debe dar **200** y en OneDrive aparece `<nombre>_firmado.pdf`.

## Qué mirar si falla

| Síntoma | Causa probable |
|---|---|
| HTTP 401 | Falta o está mal la cabecera `x-functions-key`. |
| HTTP 400 `peticion_invalida` y el mensaje nombra `pdf_base64` | La expresión de `contentBytes` quedó como texto, no como expresión, o el archivo no se adjuntó. |
| HTTP 400 `pdf_invalido` | El archivo no es PDF o está protegido con contraseña. |
| HTTP 404 `imagen_no_encontrada` | `imagen_id` distinto de `gerente.png` (la única subida en dev). |
| HTTP 413 | PDF de más de 25 MB. |
| «Analizar JSON» falla con *Invalid type* | El API devolvió un error (JSON distinto): mirar el cuerpo de la acción HTTP. Para que el flujo no se detenga, añadir a «Analizar JSON» *Configurar ejecución después de* → también «ha fallado». |
| Primera ejecución del día tarda 5–10 s | Arranque en frío de Flex Consumption; normal. |
