#!/usr/bin/env bash
# build.sh — compile the Typst resume template to a PDF.
# Layout lives in template.typ; content in a YAML file.
#
# Usage:
#   ./build.sh                                  # content.yaml       -> resume.pdf
#   ./build.sh content.acme.yaml               # tailored variant   -> content.acme.pdf
#   ./build.sh content.acme.yaml acme.pdf      # explicit output name
set -euo pipefail

cd "$(dirname "$0")"

CONTENT="${1:-content.yaml}"
if [[ ! -f "$CONTENT" ]]; then
  echo "❌ Content file not found: $CONTENT" >&2
  exit 1
fi

# Default output: resume.pdf for the master, else <content-basename>.pdf
if [[ -n "${2:-}" ]]; then
  OUT="$2"
elif [[ "$CONTENT" == "content.yaml" ]]; then
  OUT="resume.pdf"
else
  OUT="$(basename "${CONTENT%.yaml}").pdf"
fi

echo "Compiling template.typ + $CONTENT -> $OUT ..."
if typst compile --input "content=$CONTENT" template.typ "$OUT"; then
  echo "✅ Build succeeded: $(pwd)/$OUT"
else
  echo "❌ Build failed." >&2
  exit 1
fi
