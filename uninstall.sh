#!/usr/bin/env bash
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"

echo "Removing Spotify Mini Player shortcuts..."
rm -f "$APP_DIR/spotify-mini.desktop"
rm -f "$AUTOSTART_DIR/spotify-mini.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APP_DIR" 2>/dev/null || true
fi

echo "✅ Removed successfully."
