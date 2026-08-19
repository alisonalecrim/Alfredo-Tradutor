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

$AudioDevices = Get-CimInstance Win32_SoundDevice -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name
$VirtualAudio = $AudioDevices | Where-Object {
    $_ -match 'VB-Audio|VB-CABLE|Voicemeeter|Virtual Cable'
}

Write-Host ''
if ($VirtualAudio) {
    Write-Host 'Microfone virtual/cabo de áudio detectado:' -ForegroundColor Green
    $VirtualAudio | ForEach-Object { Write-Host "  - $_" }
    Write-Host 'O Alfredo vai priorizar automaticamente esse cabo como saída da Linha B.'
} else {
    Write-Host 'Nenhum cabo de áudio virtual foi detectado.' -ForegroundColor Yellow
    Write-Host 'Para a Linha B chegar ao Teams/Meet/Zoom, instale VB-CABLE ou VoiceMeeter.'
    Write-Host 'Página oficial do VB-CABLE: https://vb-audio.com/Cable/'
    Write-Host 'Depois da instalação/reinicialização, abra o Alfredo e clique em Atualizar lista.'
}

Write-Host ''
Write-Host 'Para iniciar o motor:'
Write-Host '  powershell -ExecutionPolicy Bypass -File scripts\run-engine.ps1'
