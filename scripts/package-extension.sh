#!/bin/bash
# Zip the extension folder into dist/ for the Chrome Web Store or a GitHub release.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(python3 -c "import json; print(json.load(open('$DIR/extension/manifest.json'))['version'])")"
mkdir -p "$DIR/dist"
OUT="$DIR/dist/isp-picker-extension-$VERSION.zip"
(cd "$DIR/extension" && zip -qr -X "$OUT" . -x '.*')
echo "Wrote $OUT"
