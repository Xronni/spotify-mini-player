#!/usr/bin/env bash
# Spotify Mini Player — Release Artifacts Builder
# Builds .deb package, portable .tar.gz archive, .zip, and SHA256SUMS.txt
set -e

VERSION="1.0.0"
APP_NAME="spotify-mini-player"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="$SCRIPT_DIR/dist"
RELEASE_DIR="$SCRIPT_DIR/release"
BUILD_DIR="$SCRIPT_DIR/build_artifacts"

echo "================================================================"
echo " 🎵 Building Release Artifacts for $APP_NAME v$VERSION"
echo "================================================================"

# Clean up previous builds
rm -rf "$DIST_DIR" "$BUILD_DIR"
mkdir -p "$DIST_DIR" "$BUILD_DIR" "$RELEASE_DIR"

# 1. Build .deb package
echo "==> 1. Building Debian / Ubuntu (.deb) package..."
DEB_ROOT="$BUILD_DIR/deb_root"
mkdir -p "$DEB_ROOT/DEBIAN"
mkdir -p "$DEB_ROOT/usr/bin"
mkdir -p "$DEB_ROOT/usr/share/$APP_NAME"
mkdir -p "$DEB_ROOT/usr/share/$APP_NAME/assets"
mkdir -p "$DEB_ROOT/usr/share/applications"
mkdir -p "$DEB_ROOT/usr/share/icons/hicolor/128x128/apps"
mkdir -p "$DEB_ROOT/usr/share/pixmaps"

# Control file
cat << CONTROL > "$DEB_ROOT/DEBIAN/control"
Package: $APP_NAME
Version: $VERSION
Section: sound
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-gi, python3-gi-cairo, gir1.2-gtk-4.0, gir1.2-adw-1, python3-cairo, libx11-6
Maintainer: Xronni <xronnimail@gmail.com>
Homepage: https://github.com/Xronni/$APP_NAME
Description: Modern GTK4 / Libadwaita HUD and mini player for Spotify on Linux
 Spotify Mini Player is a sleek, lightweight, semi-transparent HUD and desktop
 companion for Spotify on Linux. Built with GTK4 & Libadwaita, it provides
 real-time bi-directional MPRIS controls, volume synchronization, native LevelDB
 queue parsing, smart OSD auto-hide behavior, and automated multi-language localization.
CONTROL

# Post-install & Post-remove hooks
cat << 'POSTINST' > "$DEB_ROOT/DEBIAN/postinst"
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor || true
fi
exit 0
POSTINST
chmod 755 "$DEB_ROOT/DEBIAN/postinst"

cat << 'POSTRM' > "$DEB_ROOT/DEBIAN/postrm"
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor || true
fi
exit 0
POSTRM
chmod 755 "$DEB_ROOT/DEBIAN/postrm"

# Copy application files
cp "$SCRIPT_DIR"/*.py "$DEB_ROOT/usr/share/$APP_NAME/"
cp "$SCRIPT_DIR/style.css" "$DEB_ROOT/usr/share/$APP_NAME/"
cp "$SCRIPT_DIR/assets/icon.png" "$DEB_ROOT/usr/share/$APP_NAME/assets/"
cp "$SCRIPT_DIR/assets/icon.png" "$DEB_ROOT/usr/share/icons/hicolor/128x128/apps/$APP_NAME.png"
cp "$SCRIPT_DIR/assets/icon.png" "$DEB_ROOT/usr/share/pixmaps/$APP_NAME.png"

# Executable launcher in /usr/bin
cat << LAUNCHER > "$DEB_ROOT/usr/bin/$APP_NAME"
#!/bin/sh
exec python3 /usr/share/$APP_NAME/main.py "\$@"
LAUNCHER
chmod 755 "$DEB_ROOT/usr/bin/$APP_NAME"

# Short alias symlink (spotify-mini)
ln -sf "$APP_NAME" "$DEB_ROOT/usr/bin/spotify-mini"

# Desktop entry
cat << DESKTOP > "$DEB_ROOT/usr/share/applications/spotify-mini.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=Spotify Mini Player
GenericName=Spotify HUD & Mini Player
Comment=Sleek GTK4/Libadwaita HUD & Mini Player for Spotify
Exec=/usr/bin/$APP_NAME
Icon=$APP_NAME
Terminal=false
Categories=AudioVideo;Audio;Player;GTK;
StartupWMClass=com.github.vibe.spotifymini
Keywords=spotify;music;mini;player;osd;hud;
DESKTOP
chmod 644 "$DEB_ROOT/usr/share/applications/spotify-mini.desktop"

# Fix permissions
find "$DEB_ROOT" -type d -exec chmod 755 {} +
find "$DEB_ROOT/usr/share/$APP_NAME" -type f -exec chmod 644 {} +
chmod 755 "$DEB_ROOT/usr/share/$APP_NAME/main.py"

# Build deb
DEB_FILE="$DIST_DIR/${APP_NAME}_${VERSION}_all.deb"
dpkg-deb --build "$DEB_ROOT" "$DEB_FILE"
echo "   ✓ Built: $(basename "$DEB_FILE") ($(du -h "$DEB_FILE" | cut -f1))"

# 2. Build Portable Standalone archives
echo "==> 2. Building Standalone Portable (.tar.gz & .zip) archives..."
TAR_DIR="$BUILD_DIR/${APP_NAME}-${VERSION}"
mkdir -p "$TAR_DIR/assets"

cp "$SCRIPT_DIR"/*.py "$TAR_DIR/"
cp "$SCRIPT_DIR/style.css" "$TAR_DIR/"
cp "$SCRIPT_DIR/run.sh" "$TAR_DIR/"
cp "$SCRIPT_DIR/install.sh" "$TAR_DIR/"
cp "$SCRIPT_DIR/uninstall.sh" "$TAR_DIR/"
cp "$SCRIPT_DIR/test.sh" "$TAR_DIR/"
cp "$SCRIPT_DIR/README.md" "$TAR_DIR/"
cp "$SCRIPT_DIR/LICENSE" "$TAR_DIR/"
cp "$SCRIPT_DIR/assets/icon.png" "$TAR_DIR/assets/"

chmod +x "$TAR_DIR"/*.sh "$TAR_DIR/main.py"

# Tarball (both standard naming and backward-compatible naming)
TAR_FILE="$DIST_DIR/${APP_NAME}-v${VERSION}-linux-x86_64.tar.gz"
tar -czf "$TAR_FILE" -C "$BUILD_DIR" "${APP_NAME}-${VERSION}"
echo "   ✓ Built: $(basename "$TAR_FILE") ($(du -h "$TAR_FILE" | cut -f1))"

TAR_COMPAT="$DIST_DIR/${APP_NAME}-v${VERSION}.tar.gz"
cp "$TAR_FILE" "$TAR_COMPAT"

# Zip archives
ZIP_FILE="$DIST_DIR/${APP_NAME}-v${VERSION}.zip"
(cd "$BUILD_DIR" && zip -q -r "$ZIP_FILE" "${APP_NAME}-${VERSION}")
echo "   ✓ Built: $(basename "$ZIP_FILE") ($(du -h "$ZIP_FILE" | cut -f1))"

ZIP_PORTABLE="$DIST_DIR/${APP_NAME}-v${VERSION}-portable.zip"
cp "$ZIP_FILE" "$ZIP_PORTABLE"

# 3. Generate SHA256 checksums
echo "==> 3. Generating SHA256 Checksums..."
(cd "$DIST_DIR" && sha256sum * > SHA256SUMS.txt)
echo "   ✓ Generated SHA256SUMS.txt"

# 4. Sync to local release/ folder
cp "$DIST_DIR"/* "$RELEASE_DIR/"

# Clean temporary build directory
rm -rf "$BUILD_DIR"

echo "================================================================"
echo " 🎉 All release files successfully built in: dist/ and release/"
ls -lh "$DIST_DIR"
echo "================================================================"
