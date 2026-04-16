#!/bin/bash
# Install ipod-sync on macOS (Apple Silicon / Intel)
set -e

echo "=== ipod-sync: instalacion macOS ==="

# --- System dependencies ---
echo "Instalando dependencias del sistema..."
if ! command -v brew &>/dev/null; then
    echo "ERROR: Homebrew no encontrado. Instala desde https://brew.sh"
    exit 1
fi

brew install ffmpeg

# libgpod: Homebrew instala 0.8.x pero sin gdk-pixbuf (sin artwork).
# Para artwork completo: brew install gdk-pixbuf + compilar libgpod desde fuente.
if ! brew list libgpod &>/dev/null 2>&1; then
    echo "Intentando instalar libgpod desde Homebrew..."
    brew install libgpod 2>/dev/null || true
fi

# Si libgpod no esta disponible en Homebrew, compilar desde fuente.
LIBGPOD_PATH="/tmp/libgpod-install/usr/local/lib/libgpod.4.dylib"
if [ ! -f "$LIBGPOD_PATH" ]; then
    echo ""
    echo "AVISO: libgpod no encontrado en $LIBGPOD_PATH"
    echo "Para compilar desde fuente, consulta las instrucciones en:"
    echo "  https://github.com/fadingred/libgpod"
    echo ""
    echo "Flags de compilacion necesarios (macOS ARM64):"
    echo "  autoreconf -fi"
    echo "  ./configure --prefix=/tmp/libgpod-install --without-libimobiledevice \\"
    echo "    CFLAGS='-Wno-error -Wno-cast-align' \\"
    echo "    LDFLAGS=\"\$(pkg-config --libs gmodule-2.0)\""
    echo "  make && make install"
fi

# --- gamdl ---
echo "Instalando gamdl..."
pip install -q gamdl

# --- ipod-sync ---
echo "Instalando ipod-sync..."
cd "$(dirname "$0")/.."
pip install -e .

echo ""
echo "=== Instalacion completada ==="
echo ""
echo "Proximos pasos:"
echo "  1. Exporta tus cookies de music.apple.com (formato Netscape)"
echo "     y guardalas en: ~/.config/ipod-sync/cookies.txt"
echo "  2. Ejecuta: ipod-sync download --list-playlists"
