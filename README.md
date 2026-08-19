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

## Microfone virtual no Windows

O Alfredo suporta automaticamente cabos virtuais conhecidos, principalmente **VB-CABLE** e **VoiceMeeter**.

O fluxo recomendado com VB-CABLE é:

```text
Seu microfone físico
        ↓
Linha B do Alfredo
        ↓
Whisper → tradução → TTS
        ↓
CABLE Input (VB-Audio Virtual Cable)
        ↓
CABLE Output (VB-Audio Virtual Cable)
        ↓
Teams / Meet / Zoom / Discord
```

No Alfredo, **CABLE Input** aparece como uma saída recomendada da Linha B. Na aplicação de chamada, selecione **CABLE Output** como microfone.

O Alfredo detecta automaticamente VB-CABLE/VoiceMeeter e prioriza o cabo virtual como destino da Linha B. Isso impede que a voz traduzida seja simplesmente reproduzida nos seus alto-falantes.

> O driver do cabo virtual precisa estar instalado no Windows. O projeto não instala nem distribui drivers de terceiros. Para um produto final, um driver próprio baseado em tecnologias WDM/WaveRT (como o modelo SysVAD da Microsoft) poderá substituir o VB-CABLE sem alterar o pipeline de tradução.

## Rodar no Windows

Na raiz do projeto, abra PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-windows-deps.ps1
powershell -ExecutionPolicy Bypass -File scripts\run-engine.ps1
```

O instalador verifica se existe VB-CABLE/VoiceMeeter. Se não existir, ele informa que um cabo virtual é necessário para enviar a Linha B à chamada.

Em outro PowerShell:

```powershell
cd apps\desktop
npm install
npm run tauri dev
```

### Configuração recomendada no Windows

**Linha A — Ouvindo a outra pessoa**

- Capturar de: `Som do computador — <dispositivo usado pela call>`
- Ouvir em: seus fones/headset
- Exemplo: Inglês → Português

**Linha B — Falando para a outra pessoa**

- Capturar de: seu microfone físico
- Enviar para: `CABLE Input` / `Voicemeeter Input` marcado como **Microfone virtual da call**
- Exemplo: Português → Inglês

Depois, no Teams/Meet/Zoom/Discord, escolha o endpoint de gravação pareado como microfone, por exemplo:

```text
CABLE Output (VB-Audio Virtual Cable)
```

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
Linha B: microfone    → STT → tradução → TTS → microfone virtual da call
```

## API do motor (`http://127.0.0.1:8765`)

- `GET /health`
- `GET /devices`
- `GET /status`
- `POST /session/start`
- `POST /session/stop`
