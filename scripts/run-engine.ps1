$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$VenvPython = Join-Path $Root 'engine\.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPython)) {
    throw "Ambiente virtual não encontrado. Rode primeiro: powershell -ExecutionPolicy Bypass -File scripts\install-windows-deps.ps1"
}

$env:PYTHONPATH = $Root
& $VenvPython -m uvicorn engine.api.main:app --reload --host 127.0.0.1 --port 8765
