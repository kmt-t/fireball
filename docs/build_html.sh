#!/usr/bin/env bash
# Fireball docs -> HTML site build script (output: docs_site)
# Run this script from the docs/ directory.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTDIR="$ROOT/docs_site"

# Ensure pandoc is available
if ! command -v pandoc >/dev/null 2>&1; then
  echo "ERROR: pandoc not found in PATH." >&2
  echo "Install pandoc (https://pandoc.org) and ensure it is on the PATH, then re-run this script." >&2
  exit 1
fi

# Recreate output directory
rm -rf "$OUTDIR"
mkdir -p "$OUTDIR"

echo "Generating HTML into: $OUTDIR"
echo

# Find all markdown files and convert, excluding any previously generated site content
find "$ROOT" -name '*.md' -type f -not -path '*/docs_site/*' -print0 | while IFS= read -r -d '' md; do
  rel="${md#$ROOT/}"
  dir="$(dirname "$rel")"
  out_dir="$OUTDIR/$dir"
  mkdir -p "$out_dir"
  name="$(basename "$md" .md)"
  out="$out_dir/$name.html"

  echo "Processing: $md -> $out"
  pandoc "$md" -s -t html5 --standalone --toc --metadata=title:"$name" -o "$out"
done

echo
echo "All done."
