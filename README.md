# Alfredo - Tradutor

Tradutor de fala em tempo real para **Windows e Linux**: **Tauri (Rust)** + **motor Python** — ainda em construção.

## Estrutura

```text
Alfredo - Tradutor/
├── apps/desktop/     # UI Tauri
├── engine/           # Motor (API + pipelines A/B)
└── scripts/          # Inicialização Linux/Windows
```

## Áudio por plataforma

- **Windows:** Linha A captura o áudio do computador via **WASAPI loopback** (SoundCard). Linha B captura o microfone físico. O TTS usa Windows Speech/SAPI quando disponível.
- **Linux:** mantém captura via PortAudio/PipeWire/Pulse/ALSA e TTS via espeak-ng.

A Linha A representa **outra pessoa → você**. A Linha B representa **você → outra pessoa**.

## Rodar no Windows

Na raiz do projeto, abra PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-windows-deps.ps1
powershell -ExecutionPolicy Bypass -File scripts\run-engine.ps1
```

Em outro PowerShell:

```powershell
cd apps\desktop
npm install
npm run tauri dev
```

No Windows, na Linha A escolha um dispositivo identificado como **Som do computador — ... / WASAPI**. Ele representa o áudio que está sendo reproduzido no endpoint selecionado.

## Rodar no Linux

**Terminal 1 — motor**

```bash
source engine/.venv/bin/activate
bash scripts/run-engine.sh
```

**Terminal 2 — app**

```bash
cd apps/desktop
npm run tauri dev
```

## Modos

| Modo | O que faz |
|------|-----------|
| **Passthrough** | Eco entrada→saída por linha para teste dos dispositivos |
| **Traduzir** | STT (faster-whisper base/CPU) → tradução → TTS local |

## Pipeline

```text
Linha A: call/sistema → STT → tradução → TTS → fones
Linha B: microfone    → STT → tradução → TTS → saída da call
```

O microfone virtual dedicado da Linha B ainda é uma etapa futura do projeto; até ele existir, selecione manualmente a saída apropriada para os testes.

## API do motor (`http://127.0.0.1:8765`)

- `GET /health`
- `GET /devices`
- `GET /status`
- `POST /session/start`
- `POST /session/stop`
