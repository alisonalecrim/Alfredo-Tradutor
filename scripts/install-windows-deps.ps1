$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Python = Get-Command py -ErrorAction SilentlyContinue
if ($Python) {
    $PythonCmd = @('py', '-3')
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonCmd = @('python')
} else {
    throw 'Python 3 não encontrado. Instale Python 3.11+ e rode este script novamente.'
}

$Venv = Join-Path $Root 'engine\.venv'
if (-not (Test-Path $Venv)) {
    if ($PythonCmd.Count -eq 2) {
        & $PythonCmd[0] $PythonCmd[1] -m venv $Venv
    } else {
        & $PythonCmd[0] -m venv $Venv
    }
}

$VenvPython = Join-Path $Venv 'Scripts\python.exe'
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $Root 'engine\requirements.txt')

Write-Host ''
Write-Host 'Dependências Python instaladas.' -ForegroundColor Green
Write-Host 'O Alfredo usará WASAPI loopback para capturar o som do Windows.'
Write-Host 'Para iniciar o motor:'
Write-Host '  powershell -ExecutionPolicy Bypass -File scripts\run-engine.ps1'
