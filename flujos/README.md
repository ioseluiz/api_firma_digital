# Flujos de Power Automate como código

Los flujos de la solución `FirmadeDocumentos` (IN_DevTest) viven aquí como JSON de definición
(`clientdata` de Dataverse) y se suben con scripts. Nada se edita en el diseñador salvo el esqueleto
inicial de un flujo nuevo; si alguien lo hace, `subir_flujo.py` lo muestra en el diff antes de pisarlo.

| Archivo | Qué es |
|---|---|
| `FIRMA_F01_Registrar_envio.json` | Línea base de cpitti + el ajuste del visor (`ajustar_f01.py`) |
| `FIRMA_F02_Aprobar_y_firmar.json` | Generado por `generar_f02.py`: llama al API (`/firmar`) y guarda `Firmado.pdf` |
| `FIRMA_F03_Rechazar.json`, `FIRMA_F04_Cancelar.json`, `FIRMA_F05_Recordatorios.json` | Línea base exportada el 2026-11-23 (sin cambios) |
| `dataverse.py` | Web API de Dataverse con el token de `az` (sin SDK) |
| `validar_parametros.py` | Valida acciones contra el swagger real del conector (`pac connectivity`) y la coherencia interna |
| `subir_flujo.py` | Respaldo + diff + comprobaciones + PATCH de `clientdata`; `--crear` para un flujo nuevo |
| `variables_entorno.py` | Lista, define y fija valores de las variables `ini_FIRMA_*` |
| `fetch/*.xml` | Consultas para `pac org fetch --xmlFile` (flujos, referencias de conexión, variables, ejecuciones) |
| `swagger/`, `respaldo/` | Caché del swagger y respaldos del clientdata vivo (gitignored) |

## Procedimiento

```
# 0. una vez por máquina: az login (cuenta jlmunoz) y pac auth select del perfil IN_DevTest
python flujos/variables_entorno.py --listar                      # todas con fila de valor; SIN_CONFIGURAR es el centinela
python flujos/generar_f02.py                                     # regenera F02 desde el código
python flujos/ajustar_f01.py                                     # aplica (idempotente) el ajuste del visor a F01
python flujos/validar_parametros.py flujos/FIRMA_F0*.json        # cero hallazgos antes de subir
python flujos/subir_flujo.py --flujo flujos/FIRMA_F01_Registrar_envio.json          # solo muestra el diff
python flujos/subir_flujo.py --flujo flujos/FIRMA_F01_Registrar_envio.json --si --activar
python flujos/subir_flujo.py --flujo flujos/FIRMA_F02_Aprobar_y_firmar.json --si --activar
pac org fetch --xmlFile flujos/fetch/workflows.xml               # cinco flujos Activated, owner jlmunoz
```

Un flujo **nuevo** se crea con `subir_flujo.py --crear <json> --solucion FirmadeDocumentos --si`: lo
inserta por Web API (category 5) dentro de la solución, en borrador y con las referencias de conexión
que nombra el JSON. F02 se creó así el 2026-11-23 (`51b850f0-6ae7-f111-84e9-000d3a9cf3ce`).

## Reglas que cuestan horas si se olvidan

- **Nombres de parámetro del conector**: salen del swagger, no de la documentación. Un nombre mal
  escrito no falla al subir: falla al encender (`WorkflowOperationParametersExtraParameter`). Por eso
  `validar_parametros.py` corre antes de cada subida.
- **Variables de entorno sin fila de valor** impiden encender el flujo que las nombra
  (`XrmEnvironmentVariableAttributeNotFound`). Cadena vacía no crea fila: usar `SIN_CONFIGURAR`.
- **Apagar y encender** tras cambiar la definición o las referencias: si no, el sondeo del
  disparador se queda mudo sin ningún error. `subir_flujo.py` lo hace.
- **`runAfter` dentro de un `Scope`** solo puede apuntar a hermanos del mismo Scope.
- **`actions('X')?['outputs']`**, no `outputs('X')`, para leer una acción que pudo quedar omitida
  (p. ej. la HTTP en el Catch).
- **`concurrency.runs = 1`** en el disparador de F02 no se puede deshacer sin recrear el flujo.
- La **clave del API** vive en `ini_FIRMA_ApiKey` (String; el tipo Secreto exige Key Vault). Es la
  clave de función dedicada `powerautomate`, rotable con `az functionapp keys set` y
  `variables_entorno.py --fijar ini_FIRMA_ApiKey --desde-az`. Las acciones HTTP llevan `secureData`.

## Propiedad y licencias

Los flujos corren con el plan de su propietario principal. La acción HTTP (F01 y F02) es premium:
el propietario debe ser jlmunoz y las referencias de conexión deben apuntar a sus conexiones. El
procedimiento está en `../../docs/TRASPASO.md`.

Medido el 2026-11-23: subir la definición de F01 (que ahora lleva la acción HTTP) mientras el
propietario es cpitti devuelve `MissingAdequateQuotaPolicy … to save this flow with connection: 'Http'`.
El orden obligado es: traspaso (propietario jlmunoz + referencias re-apuntadas) → `subir_flujo.py --flujo
FIRMA_F01… --si --activar` → `--flujo FIRMA_F02… --si --activar`. Hasta entonces F01 sigue con la
definición de cpitti (sin visor) y F02 queda en borrador.
