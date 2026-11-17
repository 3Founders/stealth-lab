#!/bin/sh
# kell desktop for Linux, one line:   curl -fsSL https://www.kell.org.in/desktop.sh | sh
#
# Downloads the newest AppImage from kell.org.in/download?os=linux into ~/.local/bin, adds a menu entry and starts kell. No root,
# nothing outside your home folder. The AppImage updates itself. Prefer a package? Debian/Ubuntu: download
# https://www.kell.org.in/download?os=linux&format=deb and install it with `sudo apt install ./kell-*.deb`.
# This is not the CLI installer (that is install.sh).
set -eu

SITE="${KELL_SITE:-https://www.kell.org.in}"
SITE="${SITE%/}"
BIN="$HOME/.local/bin"
APP="$BIN/kell.AppImage"
ICON_DIR="$HOME/.local/share/icons/hicolor/512x512/apps"
DESKTOP_DIR="$HOME/.local/share/applications"

case "$(uname -s)" in Linux) ;; *) echo "This installs kell on Linux. For Windows use: irm https://www.kell.org.in/desktop.ps1 | iex" >&2; exit 1 ;; esac
case "$(uname -m)" in x86_64|amd64) ;; *) echo "kell for Linux is built for x86_64 (64-bit Intel/AMD) only for now; this computer is $(uname -m)." >&2; exit 1 ;; esac
command -v curl >/dev/null 2>&1 || { echo "curl is needed: install it (e.g. sudo apt install curl) and run this again." >&2; exit 1; }

mkdir -p "$BIN" "$ICON_DIR" "$DESKTOP_DIR"
echo "Downloading kell ..."
TMP="$APP.part"
if ! curl -fL --retry 2 --progress-bar "$SITE/download?os=linux" -o "$TMP"; then
  rm -f "$TMP"
  echo "The download failed. If your network blocks GitHub's download server (release-assets.githubusercontent.com)," >&2
  echo "open $SITE/download?os=linux in a browser, or try another network or DNS (1.1.1.1)." >&2
  exit 1
fi
SIZE=$(wc -c < "$TMP" | tr -d ' ')
if [ "$SIZE" -lt 10000000 ]; then rm -f "$TMP"; echo "The download is not the app ($SIZE bytes). Try again, or download it from $SITE/download?os=linux in a browser." >&2; exit 1; fi
mv -f "$TMP" "$APP"
chmod +x "$APP"

# AppImages need FUSE 2 to mount themselves; without it, run them extracted.
if ! ldconfig -p 2>/dev/null | grep -q 'libfuse\.so\.2'; then
  echo "Note: FUSE 2 (libfuse2) is not installed. kell will still start, but installing it is faster:"
  echo "      Ubuntu 24.04+: sudo apt install libfuse2t64     older Ubuntu/Debian: sudo apt install libfuse2"
  RUN="env APPIMAGE_EXTRACT_AND_RUN=1 $APP"
else
  RUN="$APP"
fi

# Menu entry and icon, taken from the AppImage itself.
( cd "$(mktemp -d)" && "$APP" --appimage-extract 'usr/share/icons/hicolor/512x512/apps/kell.png' >/dev/null 2>&1 \
    && cp squashfs-root/usr/share/icons/hicolor/512x512/apps/kell.png "$ICON_DIR/kell.png" ) || true
cat > "$DESKTOP_DIR/kell.desktop" <<EOF
[Desktop Entry]
Name=kell
Comment=The cheapest model that can do it, with the right tool
Exec=$RUN %U
Icon=kell
Terminal=false
Type=Application
Categories=Development;Utility;
StartupWMClass=kell
EOF
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true

echo "Installed to $APP"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "Tip: add $BIN to your PATH to start kell by typing kell.AppImage." ;; esac
echo "Starting kell. Sign in when the window opens."
( $RUN >/dev/null 2>&1 & ) || true
