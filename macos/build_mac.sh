#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ "$(uname -s)" != Darwin ]]; then echo 'This build requires macOS.' >&2; exit 1; fi
DY_MAC_ARCH="${1:-$(uname -m)}"
case "$DY_MAC_ARCH" in arm64|x86_64) ;; *) echo 'Use arm64 or x86_64' >&2; exit 1;; esac
export DY_MAC_ARCH
MAC_PYTHON="${MAC_PYTHON:-python3}"
"$MAC_PYTHON" -c 'import platform,sys,tkinter;assert platform.machine()==sys.argv[1],"Use Python matching target architecture"' "$DY_MAC_ARCH"
"$MAC_PYTHON" -m venv ".venv-$DY_MAC_ARCH"
MAC_VENV=".venv-$DY_MAC_ARCH/bin/python"
"$MAC_VENV" -m pip install -r requirements-build.txt
"$MAC_VENV" -c 'import tkinter;print("Tk available")'
# Installed Chrome/Edge are used; do not bundle a separate browser or user profiles.
MAC_ICON_WORK="$(mktemp -d)"
mkdir "$MAC_ICON_WORK/app.iconset"
for size in 16 32 128 256 512; do
    sips -z "$size" "$size" assets/app.png --out "$MAC_ICON_WORK/app.iconset/icon_${size}x${size}.png" >/dev/null
done
for size in 16 32 128 256 512; do
    sips -z "$((size*2))" "$((size*2))" assets/app.png --out "$MAC_ICON_WORK/app.iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$MAC_ICON_WORK/app.iconset" -o assets/app.icns
"$MAC_VENV" -m PyInstaller --noconfirm --clean --distpath "dist/$DY_MAC_ARCH" --workpath "build/$DY_MAC_ARCH" dy_live_mac.spec
MAC_APP="dist/$DY_MAC_ARCH/DY直播间自助工具.app"
codesign --verify --deep --strict "$MAC_APP"
MAC_NATIVE_ZIP="$MAC_ICON_WORK/native.zip"
MAC_FINAL_ZIP="dist/DYLiveAssistantMac-6.2.39-$DY_MAC_ARCH.zip"
ditto -c -k --keepParent "$MAC_APP" "$MAC_NATIVE_ZIP"
"$MAC_VENV" tools/normalize_mac_zip.py "$MAC_NATIVE_ZIP" "$MAC_FINAL_ZIP"
mkdir "$MAC_ICON_WORK/verify"
ditto -x -k "$MAC_FINAL_ZIP" "$MAC_ICON_WORK/verify"
codesign --verify --deep --strict "$MAC_ICON_WORK/verify/DY直播间自助工具.app"
echo "Built $MAC_APP. Native macOS validation is still required before release."
