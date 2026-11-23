# Levanta el API de firma en local: http://127.0.0.1:7071/api/v1/salud
# Uso:  .\iniciar.ps1          (desde la carpeta api-ini-firma)
# Ctrl+C para detenerlo.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creando entorno virtual con Python 3.12..." -ForegroundColor Cyan
    py -3.12 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -q -r requirements-dev.txt
}
if (-not (Test-Path "local.settings.json")) {
    Copy-Item local.settings.json.example local.settings.json
}
if (-not (Test-Path "firmas_local\gerente.png")) {
    .\.venv\Scripts\python.exe scripts\generar_firma_prueba.py
}
if (-not (Get-Command func -ErrorAction SilentlyContinue)) {
    Write-Host "Azure Functions Core Tools no está instalado. Instalar sin administrador con:" -ForegroundColor Yellow
    Write-Host "    npm install -g azure-functions-core-tools@4"
    exit 1
}

& .\.venv\Scripts\Activate.ps1
Write-Host ""
Write-Host "API de firma en http://127.0.0.1:7071  (salud: /api/v1/salud, firmar: /api/v1/firmar)" -ForegroundColor Green
Write-Host "Probar:  python scripts\firmar_local.py --pdf salida\memorando.pdf --ancla 'Ing. Juan Pérez'" -ForegroundColor Gray
Write-Host ""
func start --port 7071
