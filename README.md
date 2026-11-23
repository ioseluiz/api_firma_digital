# api-ini-firma — servicio de firma para INI Firma

Azure Function (Python 3.12) que recibe un PDF, le incrusta la imagen de la firma del gerente
(plana, en el contenido de la página; opcionalmente con un sello de texto) y devuelve el PDF
firmado con su hash. Es la pieza en Azure del plan
`../PLAN.md` (sección 8). La llama el flujo **F02 Aprobar y firmar** de Power Automate con una
acción HTTP.

**Sin estado y sin permisos sobre SharePoint:** el PDF viaja en el cuerpo de la petición y vuelve
en la respuesta. El servicio no guarda documentos ni registra su contenido.

## Estructura

```
api-ini-firma/
├─ function_app.py           rutas HTTP: POST v1/firmar, GET v1/salud
├─ firma/
│  ├─ estampar.py            lógica pura: bytes + parámetros → bytes (PyMuPDF)
│  ├─ modelos.py             contrato JSON (pydantic)
│  ├─ imagenes.py            origen de las imágenes de firma: carpeta local o Blob privado
│  └─ errores.py             códigos de error ↔ HTTP
├─ tests/                    pytest; los PDFs y la imagen se generan en memoria
├─ scripts/
│  ├─ generar_firma_prueba.py  crea firmas_local/*.png de PRUEBA
│  └─ firmar_local.py          cliente CLI para probar el API con un PDF real
├─ infra/main.bicep          Function (Flex Consumption), Storage, Key Vault, App Insights, roles
├─ host.json · requirements.txt · local.settings.json.example
└─ firmas_local/             (ignorada por git) imágenes de firma para desarrollo
```

## Desarrollo local

Requisitos: Python 3.12 (`py -3.12`), [Azure Functions Core Tools v4](https://learn.microsoft.com/azure/azure-functions/functions-run-local)
para correr el host, Azure CLI para desplegar.

Core Tools **sin permisos de administrador** (el MSI de winget pide elevación; npm instala por usuario):

```powershell
npm install -g azure-functions-core-tools@4
func --version        # 4.16.x
```

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python scripts\generar_firma_prueba.py          # firmas_local\gerente.png y suplente.png (de prueba)
Copy-Item local.settings.json.example local.settings.json
pytest                                          # 40 pruebas, < 2 s
func start                                      # http://localhost:7071/api/v1/salud
```

Probar con un PDF real (el host local no exige clave):

```powershell
python scripts\firmar_local.py --pdf memo.pdf --ancla "Firma del Gerente:"
python scripts\firmar_local.py --pdf memo.pdf --pagina 0 --x-mm 120 --y-mm 40 --ancho-mm 50
python scripts\firmar_local.py --pdf memo.pdf --solo-json      # imprime la petición para copiarla al flujo
```

Usa `http://127.0.0.1:7071`, no `localhost`: en Windows `localhost` intenta IPv6 primero y cada
petición tarda ~2 s de más (medido: 220 ms de conexión frente a 1 ms).

### Probar con Postman

En `postman/` hay una colección con nueve peticiones (salud, tres variantes de firma que descargan el
PDF, una con sello, cuatro casos de error) con sus aserciones. El documento viaja en una variable de
entorno que se genera con un script, para no pegar base64 a mano:

```powershell
.\.venv\Scripts\python.exe postman\generar_entorno.py --pdf salida\mi_documento.pdf --imagen-id mifirma.png --ancla "Gerente INIG"
```

Luego en Postman: *Import* → arrastrar `postman\INI_Firma.postman_collection.json` y
`postman\INI_Firma.local.postman_environment.json` → seleccionar el entorno «INI Firma (local)»
arriba a la derecha → abrir la colección y *Run* (o enviar cada petición). Las peticiones que llevan
`Accept: application/pdf` devuelven el archivo: con **Send and Download** (flecha junto a *Send*)
se guarda el PDF firmado y se abre directo.

Para apuntar a Azure, generar otro entorno con `--base-url https://func-ini-firma-dev.azurewebsites.net --clave <function key> --nombre dev`.
Los archivos de entorno están en `.gitignore` porque contienen el documento.

Desde línea de comandos, la misma colección corre con [newman](https://www.npmjs.com/package/newman):
`newman run postman\INI_Firma.postman_collection.json -e postman\INI_Firma.local.postman_environment.json`.

**Respuesta como archivo.** Cualquier petición a `/firmar` con cabecera `Accept: application/pdf`
recibe el PDF firmado como binario (`Content-Disposition: attachment`) y los datos del JSON en
cabeceras `X-Sha256`, `X-Codigo-Verificacion`, `X-Paginas`, `X-Pagina-Firmada`, `X-Ancla-Encontrada`,
`X-Duracion-Ms`. Los errores siguen siendo JSON. El flujo de Power Automate usa siempre el JSON.

Sin instalar `func`, la lógica se prueba igual con `pytest` (las pruebas llaman a las funciones
directamente). `scripts/firmar_local.py` necesita el host corriendo o la URL de Azure.

## Contrato

### `POST /api/v1/firmar`

Cabeceras: `Content-Type: application/json`, `X-Correlation-Id` (opcional; se devuelve igual y se
registra; el flujo manda el nombre de su ejecución). Autenticación: ver más abajo.

`pdf_base64` acepta, además de la cadena base64 limpia, lo que suelen mandar los conectores de
Power Automate: el objeto binario `{"$content-type": ..., "$content": "<base64>"}`, un data URI,
base64 con saltos de línea, base64 doble y base64 del propio objeto binario (las dos últimas son lo
que a veces produce `base64(contentBytes)` en el desencadenador manual). Lo único que rechaza con 400 es el PDF decodificado (`%PDF-...` como texto),
porque esos bytes ya llegan rotos.

Petición mínima (solo la imagen, en la última página, posición por defecto):

```json
{
  "solicitud_id": 123,
  "codigo": "FIRMA-2026-000123",
  "pdf_base64": "<PDF en base64>",
  "firmante": { "imagen_id": "gerente.png" },
  "fecha_firma": "2026-11-18T14:05:12"
}
```

Petición completa:

```json
{
  "solicitud_id": 123,
  "codigo": "FIRMA-2026-000123",
  "pdf_base64": "<PDF en base64>",
  "firmante": {
    "nombre": "Ing. Juan Pérez",
    "cargo": "Gerente de la División de Ingeniería",
    "email": "jperez@pancanal.com",
    "imagen_id": "gerente.png"
  },
  "fecha_firma": "2026-11-18T14:05:12",
  "posicion": {
    "pagina": 0,
    "x_mm": 120, "y_mm": 40, "ancho_mm": 50,
    "ancla_texto": "Gerente INIG",
    "ancla_alineacion": "centro",
    "si_no_hay_ancla": "posicion_fija"
  },
  "sello": { "texto": false, "codigo_verificacion": true }
}
```

| Campo | Notas |
|---|---|
| `codigo` | Código de la solicitud; va a los metadatos del PDF (y al sello, si se pide). |
| `firmante.imagen_id` | Nombre del archivo en el origen de imágenes (`gerente.png`, `suplente.png`). Solo letras, dígitos, `_ . -`. Lo decide el flujo según `FIRMA_Roles`. |
| `firmante.nombre`, `cargo` | Opcionales. Solo se imprimen si `sello.texto` es `true`. |
| `fecha_firma` | Hora local de Panamá en ISO 8601, sin zona. Entra en el código de verificación y, si hay sello, se imprime. |
| `posicion.pagina` | `0` = última página; `n` = página `n` (base 1). |
| `posicion.x_mm`, `y_mm` | Posición fija: desde el borde izquierdo y desde el borde **inferior** hasta la base de la imagen. |
| `posicion.ancho_mm` | Ancho de la imagen; el alto se calcula con la proporción del PNG. |
| `posicion.ancla_texto` | Si se indica y aparece en la página, la firma se apoya sobre la **última** aparición. |
| `posicion.ancla_alineacion` | `centro` (por defecto): centrada sobre el rótulo, para bloques «línea y debajo el cargo». `izquierda`: alineada al borde izquierdo del rótulo, para «Firma: ______». |
| `posicion.si_no_hay_ancla` | `posicion_fija` (por defecto: usa `x_mm`/`y_mm` y responde `ancla_encontrada: false`) o `error` (responde 422). |
| `sello.texto` | **Por defecto `false`: solo la imagen.** Con `true` imprime bajo la firma «Firmado electrónicamente por {nombre}, {cargo} · {fecha} · {codigo} · Verif. {verif}», buscando una franja sin texto. `sello.codigo_verificacion` controla si va el código. |

Si la firma se saliera de la página, se desplaza hacia adentro con 5 mm de margen (no se encoge).

**La imagen queda plana.** Se dibuja en el flujo de contenido de la página (un XObject de imagen),
no como anotación ni campo de formulario: un visor no la puede seleccionar, mover ni eliminar, igual
que si el documento se hubiera impreso con la firma. Para quitarla habría que editar el PDF.

Respuesta `200`:

```json
{
  "ok": true,
  "codigo": "FIRMA-2026-000123",
  "pdf_firmado_base64": "<PDF firmado en base64>",
  "sha256": "9f2c…",
  "codigo_verificacion": "3A7F0C21B9E4",
  "paginas": 5,
  "pagina_firmada": 5,
  "ancla_encontrada": true,
  "firmante": "Ing. Juan Pérez",
  "fecha_firma": "2026-11-18T14:05:12",
  "duracion_ms": 430
}
```

**Dos hashes, dos usos:**

- `sha256` es el hash del archivo firmado tal como se devuelve. Se guarda en la fila de la
  solicitud (`Hash_Firmado`) y en la bitácora; identifica el archivo final.
- `codigo_verificacion` son 12 caracteres derivados de SHA-256(original + `codigo` + `fecha_firma`).
  Se estampa en el sello porque el hash del archivo final no puede ir dentro del propio archivo.
  Con el original guardado en SharePoint cualquiera puede recomputarlo
  (`firma.estampar.codigo_verificacion`) y comprobar que ese sello corresponde a ese original.

Errores (siempre JSON):

```json
{ "ok": false, "error": "pdf_invalido", "mensaje": "El PDF está cifrado…", "correlation_id": "…" }
```

| HTTP | `error` | Cuándo |
|---|---|---|
| 400 | `peticion_invalida` | JSON inválido o campos faltantes/incorrectos; el mensaje lista los campos. |
| 400 | `pdf_invalido` | No es PDF, está dañado, cifrado con contraseña, es formulario XFA, o la imagen no se pudo leer. |
| 400 | `pagina_fuera_de_rango` | `pagina` mayor que el número de páginas. |
| 401 / 403 | — | Sin clave de función o sin token válido (lo gestiona la plataforma, no este código). |
| 404 | `imagen_no_encontrada` | `imagen_id` no existe en el origen. |
| 413 | `pdf_demasiado_grande` | Más de `FIRMA_MAX_PDF_MB` (25 por defecto). |
| 422 | `ancla_no_encontrada` | Se pidió ancla con `si_no_hay_ancla: "error"` y no aparece. |
| 500 | `error_interno` | Buscar el `correlation_id` en Application Insights. |

### `GET /api/v1/salud`

```json
{ "ok": true, "version": "0.1.0", "origen_imagenes": "blob", "imagen_por_defecto": "gerente.png",
  "imagen_por_defecto_ok": true, "hora_servidor_utc": "2026-11-19T15:02:10+00:00" }
```

Responde `503` si la imagen por defecto no es accesible. Es lo que vigila el monitor de disponibilidad.

## Lo que hace el estampado

1. Rechaza lo que no se puede firmar bien: no-PDF, cifrado, XFA, sin páginas.
2. Calcula el código de verificación sobre el original.
3. Elige la página (`0` = última) y el rectángulo (ancla o posición fija), ajustado dentro de la página.
4. Inserta el PNG (fondo transparente) en el contenido de la página. Solo si `sello.texto` es
   `true`, escribe debajo el sello en Helvetica 6.5 pt gris:
   `Firmado electrónicamente por {nombre}{cargo} · {fecha} · {codigo} · Verif. {verif}`
   (plantilla configurable con `FIRMA_TEXTO_SELLO`), en una franja libre de texto: baja por debajo
   de lo que estorbe; si no hay sitio, encima; si tampoco, a la derecha.
5. Escribe el código en los metadatos (`Keywords`) y guarda con compresión.

Tiempo típico: menos de 1 s para 50 páginas. La imagen se cachea en memoria 10 minutos.

## Configuración (variables de aplicación)

| Variable | Valor | Uso |
|---|---|---|
| `FIRMAS_ORIGEN` | `local` / `blob` | De dónde salen las imágenes. |
| `FIRMAS_CARPETA` | `firmas_local` | Solo con `local`. |
| `FIRMAS_STORAGE_URL` | `https://<cuenta>.blob.core.windows.net/` | Solo con `blob`. Se lee con la identidad administrada. |
| `FIRMAS_CONTENEDOR` | `firmas` | Solo con `blob`. |
| `FIRMAS_IMAGEN_POR_DEFECTO` | `gerente.png` | La que comprueba `/salud`. |
| `FIRMA_MAX_PDF_MB` | `25` | Límite del PDF decodificado. |
| `FIRMA_TEXTO_SELLO` | plantilla | Marcadores: `{nombre}`, `{cargo}` (ya lleva la coma), `{fecha}`, `{codigo}`, `{verif}`. |

## Despliegue en Azure

Infraestructura con Bicep (`infra/main.bicep`): cuenta de almacenamiento sin claves compartidas
(todo por identidad), contenedores `firmas` y `app-package`, Log Analytics + Application Insights,
Key Vault con RBAC, plan Flex Consumption y Function App Linux Python 3.12 con identidad
administrada y los roles mínimos.

```powershell
az login
az provider register -n Microsoft.KeyVault; az provider register -n Microsoft.Insights   # una vez por suscripción
az group create -n rg-ini-firma-dev -l eastus2 --tags division=INI sistema="INI Firma" entorno=dev
az deployment group what-if -g rg-ini-firma-dev -f infra/main.bicep -p infra/dev.bicepparam
az deployment group create  -g rg-ini-firma-dev -f infra/main.bicep -p infra/dev.bicepparam
func azure functionapp publish func-ini-firma-dev --python
```

`func publish` busca `az` en el PATH para autenticarse: si la CLI está en el entorno virtual,
anteponer su carpeta (`$env:PATH = "$env:USERPROFILE\.venv-azcli\Scripts;$env:PATH"`).

**Desplegado en `dev` el 2026-11-23** en la suscripción Visual Studio Professional del autor
(tenant `pancanal.onmicrosoft.com`, grupo `rg-ini-firma-dev`, eastus2). URL:
`https://func-ini-firma-dev.azurewebsites.net/api/v1/firmar`. La colección de Postman pasa completa
contra Azure (9 peticiones, 23 aserciones; ~90 ms por firma con el host caliente, hasta 5 s en
arranque en frío).

Azure CLI **sin permisos de administrador** (el MSI pide elevación; pip instala por usuario en un
entorno virtual aparte, y `az bicep install` descarga el compilador a `%USERPROFILE%\.azure\bin`):

```powershell
py -3.12 -m venv $env:USERPROFILE\.venv-azcli
& $env:USERPROFILE\.venv-azcli\Scripts\pip install azure-cli
Set-Alias az $env:USERPROFILE\.venv-azcli\Scripts\az.bat     # o agregar esa carpeta al PATH
az bicep install
az bicep build -f infra/main.bicep --stdout | Out-Null     # compila sin errores ni avisos (2026-11-19)
az bicep lint  -f infra/main.bicep
```

> `main.bicep` compila limpio con Bicep 0.49. Lo que el compilador no puede comprobar (cuotas de
> la suscripción, que la región ofrezca Flex Consumption, nombres de storage libres) lo dirá el
> `what-if` del primer despliegue.

Subir las imágenes de firma (solo TI, con rol *Storage Blob Data Contributor* sobre el contenedor):

```powershell
az storage blob upload --auth-mode login --account-name stinifirmadev -c firmas -n gerente.png -f C:\ruta\segura\gerente.png
```

Comprobar: `GET https://func-ini-firma-dev.azurewebsites.net/api/v1/salud?code=<clave>` debe responder `imagen_por_defecto_ok: true`.

### CI/CD (GitHub Actions)

`.github/workflows/deploy-dev.yml`: cada push a `main` corre `pytest` en Ubuntu y, si pasa, publica
en `func-ini-firma-dev` con compilación remota y comprueba `/salud`. También se lanza a mano desde
la pestaña *Actions*. Repositorio: https://github.com/ioseluiz/api_firma_digital.

Autenticación sin secretos: la identidad administrada `id-github-ini-firma` (en `rg-ini-firma-dev`,
rol Colaborador sobre ese grupo) tiene credenciales federadas para el subject OIDC que GitHub
presenta, que incluye los IDs numéricos del dueño y del repositorio
(`repo:ioseluiz@42367982/api_firma_digital@1468868023:environment:dev`). Si el repositorio cambia
de nombre o de dueño, el run falla con `AADSTS700213` y hay que recrear la credencial con el subject
que aparece en ese error:

```powershell
az identity federated-credential create -g rg-ini-firma-dev --identity-name id-github-ini-firma -n <nombre> `
  --issuer https://token.actions.githubusercontent.com --subject "<subject del error>" --audiences api://AzureADTokenExchange
```

Para un ambiente `prod` se repite el patrón: otra identidad (o la misma) con Colaborador sobre
`rg-ini-firma-prod`, un `environment: prod` en GitHub con aprobación manual y su credencial federada.

### Autenticación

- **Piloto:** `AuthLevel.FUNCTION`. El flujo manda la clave en la cabecera `x-functions-key`
  (variable de entorno secreta en Power Platform). La clave se obtiene con
  `az functionapp keys list -g rg-ini-firma-dev -n func-ini-firma-dev`.
- **Producción:** Easy Auth con Entra ID. El administrador de Entra crea la app registration
  `api-ini-firma` (expone `api://ini-firma`, rol de aplicación `Firma.Ejecutar`) y un cliente con
  secreto para Power Automate. Se configura *Authentication* en la Function App exigiendo tokens de
  esa audiencia; la acción HTTP del flujo usa *Active Directory OAuth* (tenant, audience, client id,
  secret). Con eso se puede pasar el código a `AuthLevel.ANONYMOUS` o dejar la clave como segunda capa.
  Si se usa Easy Auth, verificar además el rol `Firma.Ejecutar` en el token (claim `roles`), ya sea
  en la configuración de Easy Auth o en código (pendiente, Fase 2).

### Qué registra el servicio

Por petición: código, id de solicitud, tamaños de entrada y salida, páginas, página firmada, si
hubo ancla, duración y `correlation_id`. **Nunca** el contenido ni el nombre del documento.

## Decisiones

- **PyMuPDF** (licencia AGPL) para abrir, buscar texto, insertar imagen y texto. Uso interno sin
  redistribución del servicio; si Asesoría Legal lo objeta, `pypdf` + `reportlab` cubren lo mismo con
  más código. `pyHanko` queda reservado para una firma PAdES con certificado (v2).
- **El servicio no decide quién firma.** El flujo manda `imagen_id` y los datos del sello según
  `FIRMA_Roles` tras verificar el `Editor` de la fila. Aquí solo se valida que el id no lleve rutas.
- **Firma electrónica simple.** Imagen + sello + hashes + bitácora. No es firma calificada (Ley 51
  de 2008); ver `../PLAN.md` §4.3.
