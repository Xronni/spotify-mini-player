#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
ICON_PATH="$SCRIPT_DIR/assets/icon.png"

echo "🎵 Installing Spotify Mini Player desktop shortcut..."
mkdir -p "$APP_DIR"

DESKTOP_FILE="$APP_DIR/spotify-mini.desktop"

cat << INNER_EOF > "$DESKTOP_FILE"
[Desktop Entry]
Version=1.0
Type=Application
Name=Spotify Mini Player
Comment=Sleek GTK4/Libadwaita HUD & Mini Player for Spotify
Path=$SCRIPT_DIR
Exec=/bin/bash "$SCRIPT_DIR/run.sh"
Icon=$ICON_PATH
Terminal=false
Categories=AudioVideo;Audio;Player;
StartupWMClass=com.github.vibe.spotifymini
Keywords=spotify;music;mini;player;osd;hud;
INNER_EOF

chmod +x "$DESKTOP_FILE"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APP_DIR" 2>/dev/null || true
fi

echo "✅ Installed successfully!"
echo "You can now launch 'Spotify Mini Player' from your application menu or run: $SCRIPT_DIR/run.sh"
