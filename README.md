# Alfredo - Tradutor

Tradutor de fala em tempo real para Linux: **Tauri (Rust)** + **motor Python**.

## Estrutura

```text
Alfredo - Tradutor/
├── apps/desktop/     # UI Tauri
├── engine/           # Motor (API + pipelines A/B)
└── scripts/
```

## Rodar

**Terminal 1 — motor**

```bash
cd "/home/alison/Alfredo - Tradutor"
source engine/.venv/bin/activate
bash scripts/run-engine.sh
```

**Terminal 2 — app**

```bash
source "$HOME/.cargo/env"
cd "/home/alison/Alfredo - Tradutor/apps/desktop"
npm run tauri dev
```

## Modos

| Modo | O que faz |
|------|-----------|
| **Passthrough** | Eco entrada→saída por linha (teste de devices, linhas isoladas) |
| **Traduzir** | STT (Whisper base/CPU) → tradução → TTS (espeak-ng) |

## API do motor (`http://127.0.0.1:8765`)

- `GET /health`
- `GET /devices`
- `GET /status`
- `POST /session/start`
- `POST /session/stop`
