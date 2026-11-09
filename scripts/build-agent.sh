#!/bin/bash
# Build the Android phone agent (static linux/arm64 binary) into agent/build/.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
GO="$(command -v go || echo /usr/local/go/bin/go)"
[ -x "$GO" ] || { echo "Go is not installed: https://go.dev/dl/" >&2; exit 1; }
cd "$DIR/agent"
GOOS=linux GOARCH=arm64 CGO_ENABLED=0 "$GO" build -trimpath -ldflags="-s -w" -o build/agent-arm64 .
echo "Built $DIR/agent/build/agent-arm64"
