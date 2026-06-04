#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# run.sh — One-command detection pipeline + API ingestion
#
# Usage:
#   ./pipeline/run.sh                          # Process all clips, write events
#   ./pipeline/run.sh --ingest                 # Process + ingest into API
#   ./pipeline/run.sh --camera CAM_ENTRY_01    # Process single camera
#   ./pipeline/run.sh --realtime --ingest      # Simulated real-time + ingest
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

echo "═══════════════════════════════════════════════════════════════"
echo "  Store Intelligence Detection Pipeline"
echo "  Store: STORE_BLR_002 (Purplle Brigade Road Bangalore)"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# Check if clips directory exists
CLIPS_DIR="${CLIPS_DIR:-$(dirname "$PROJECT_ROOT")/CCTV Footage}"
if [ ! -d "$CLIPS_DIR" ]; then
    echo "ERROR: CCTV clips not found at: $CLIPS_DIR"
    echo "Set CLIPS_DIR environment variable to the clips directory."
    exit 1
fi
echo "Clips directory: $CLIPS_DIR"
echo ""

# Run detection pipeline
echo "Starting detection pipeline..."
export CLIPS_DIR
python3 -m pipeline.detect "$@"

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Pipeline complete!"
echo "  Events written to: data/events.jsonl"
echo "═══════════════════════════════════════════════════════════════"
