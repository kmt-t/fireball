#!/bin/bash
# Memory profiling with Valgrind

set -e

BINARY="${1:-./builddir/fireball}"
OUTPUT_DIR="${2:-.}"

if [ ! -f "$BINARY" ]; then
  echo "ERROR: Binary not found: $BINARY"
  exit 1
fi

echo "=== Memory Profiling with Valgrind ==="
echo "Binary: $BINARY"
echo "Output: $OUTPUT_DIR"

# Run Valgrind with massif tool
valgrind \
  --tool=massif \
  --massif-out-file="$OUTPUT_DIR/massif.out" \
  --max-snapshots=100 \
  "$BINARY"

# Generate report
ms_print "$OUTPUT_DIR/massif.out" > "$OUTPUT_DIR/memory_report.txt"

echo "✓ Memory report generated: $OUTPUT_DIR/memory_report.txt"

# Extract peak memory usage
PEAK=$(grep "peak" "$OUTPUT_DIR/memory_report.txt" | head -1)
echo "Peak memory: $PEAK"
