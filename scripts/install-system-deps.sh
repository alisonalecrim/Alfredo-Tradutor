#!/usr/bin/env bash
# Dependências de sistema (Ubuntu/Debian) para Alfredo = Tauri + áudio + build Rust.
# Precisa de sudo. Rode uma vez no terminal:
#   bash scripts/install-system-deps.sh

set -euo pipefail

echo "==> Atualizando índices apt..."
sudo apt-get update

echo "==> Instalando toolchain C, Tauri (WebKit/GTK) e áudio..."
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  build-essential \
  curl \
  wget \
  file \
  pkg-config \
  libssl-dev \
  libgtk-3-dev \
  libwebkit2gtk-4.1-dev \
  libayatana-appindicator3-dev \
  librsvg2-dev \
  patchelf \
  libxdo-dev \
  libpipewire-0.3-dev \
  portaudio19-dev \
  libasound2-dev \
  espeak-ng \
  python3-pip \
  python3-venv \
  python3-dev

echo ""
echo "OK. Dependências de sistema instaladas."
echo "Próximos passos:"
echo "  1) source \"\$HOME/.cargo/env\""
echo "  2) source engine/.venv/bin/activate"
echo "  3) cd apps/desktop && npm run tauri dev"
echo ""
echo "Testar o motor:"
echo "  source engine/.venv/bin/activate"
echo "  uvicorn engine.api.main:app --app-dir . --reload --port 8765"
