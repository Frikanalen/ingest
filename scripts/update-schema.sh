#!/usr/bin/env bash
set -euo pipefail

URL="${1:-http://localhost:8000/api/schema}"
OUTPUT="${2:-schema.yaml}"

if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$URL" -o "$OUTPUT"
elif command -v wget >/dev/null 2>&1; then
  wget -q -O "$OUTPUT" "$URL"
else
  echo "Error: neither curl nor wget is installed." >&2
  exit 1
fi

echo "Schema updated: $OUTPUT"
echo "To regenerate the Python client, run: ./scripts/generate-client.sh"
