
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

EXECUTA_JSON="executa.json"
ENTRY_FILE="exam_pack_plugin.py"
OUT_DIR="dist-anna"

if [ ! -f "$EXECUTA_JSON" ]; then
  echo "ERROR: $EXECUTA_JSON not found" >&2
  exit 1
fi

if [ ! -f "$ENTRY_FILE" ]; then
  echo "ERROR: $ENTRY_FILE not found" >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv is required" >&2
  exit 1
fi

TOOL_ID="$(python3 -c 'import json; print(json.load(open("executa.json"))["tool_id"])')"
VERSION="$(python3 -c 'import json; print(json.load(open("executa.json")).get("version", "0.0.0"))')"
DISPLAY_NAME="$(python3 -c 'import json; d=json.load(open("executa.json")); print(d.get("name") or d["tool_id"])')"
DESCRIPTION="$(python3 -c 'import json; print(json.load(open("executa.json")).get("description", ""))')"

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"

case "$ARCH" in
  x86_64|amd64) ARCH="x86_64" ;;
  arm64|aarch64) ARCH="arm64" ;;
esac

case "$OS-$ARCH" in
  darwin-arm64) PLATFORM="darwin-arm64" ;;
  darwin-x86_64) PLATFORM="darwin-x86_64" ;;
  linux-x86_64) PLATFORM="linux-x86_64" ;;
  *)
    echo "ERROR: unsupported platform: $OS-$ARCH" >&2
    exit 1
    ;;
esac

echo "Tool ID: $TOOL_ID"
echo "Version: $VERSION"
echo "Platform: $PLATFORM"

rm -rf build dist "$OUT_DIR/staging-$PLATFORM"
mkdir -p "$OUT_DIR/staging-$PLATFORM/bin"

echo "Building executable with PyInstaller..."

uv run --with pyinstaller python -m PyInstaller \
  --onefile \
  --clean \
  --noupx \
  --name "$TOOL_ID" \
  "$ENTRY_FILE"

BINARY="dist/$TOOL_ID"

if [ ! -f "$BINARY" ]; then
  echo "ERROR: binary was not created" >&2
  exit 1
fi

if [ "$(uname -s)" = "Darwin" ]; then
  codesign --force --sign - "$BINARY" 2>/dev/null || true
fi

STAGE="$OUT_DIR/staging-$PLATFORM"
cp "$BINARY" "$STAGE/bin/$TOOL_ID"
chmod 0755 "$STAGE/bin/$TOOL_ID"

python3 - "$STAGE/manifest.json" "$TOOL_ID" "$VERSION" "$DISPLAY_NAME" "$DESCRIPTION" <<'PY'
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
tool_id = sys.argv[2]
version = sys.argv[3]
display_name = sys.argv[4]
description = sys.argv[5]

entrypoint = f"bin/{tool_id}"

manifest = {
    "name": tool_id,
    "display_name": display_name,
    "version": version,
    "description": description,
    "runtime": {
        "binary": {
            "entrypoint": {
                "default": entrypoint
            },
            "permissions": {
                entrypoint: "0o755"
            }
        }
    }
}

manifest_path.write_text(
    json.dumps(manifest, indent=2) + "\n",
    encoding="utf-8"
)
PY

ARCHIVE="$OUT_DIR/$TOOL_ID-$PLATFORM.tar.gz"

echo "Creating archive: $ARCHIVE"

(
  cd "$STAGE"
  tar czf "../$TOOL_ID-$PLATFORM.tar.gz" .
)

if command -v shasum >/dev/null 2>&1; then
  SHA256="$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')"
else
  SHA256="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
fi

SIZE="$(wc -c < "$ARCHIVE" | tr -d ' ')"

echo
echo "Built archive: $ARCHIVE"
echo "SHA-256: $SHA256"
echo "Size: $SIZE bytes"
echo
echo "Archive contents:"
tar tzf "$ARCHIVE"
echo
echo "Binary package created successfully."
