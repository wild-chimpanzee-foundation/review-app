#!/usr/bin/env bash
# Reproducible documentation screenshots.
#
# Runs in two phases, each against its own throwaway data dir (the real user
# data dir is never touched — XDG_DATA_HOME is redirected to a temp location):
#
#   Phase A — empty database: drives the first-run setup wizard.
#   Phase B — seeded demo database: the login screen, the guided tour, and the
#             four main pages (dashboard, review, import/export, settings).
#
# Usage:  scripts/screenshots/capture.sh
# Output: userdocs/img/*.jpg
#
# Requirements: ffmpeg, chromium + chromedriver on PATH, uv.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# Uncommon port to avoid clashing with a dev server or `mkdocs serve` (8000).
PORT="${PORT:-8099}"
BASE_URL="http://127.0.0.1:${PORT}"
OUT_DIR="userdocs/img"
ROOT="$(mktemp -d /tmp/review-demo.XXXXXX)"

APP_PID=""
stop_app() {
  [ -n "$APP_PID" ] && kill "$APP_PID" 2>/dev/null || true
  APP_PID=""
  # Wait for the port to be released before the next launch.
  for _ in $(seq 1 20); do
    curl -fsS "${BASE_URL}/" >/dev/null 2>&1 || break
    sleep 0.3
  done
}
cleanup() {
  stop_app
  rm -rf "$ROOT"
}
trap cleanup EXIT

launch_app() { # $1 = XDG_DATA_HOME
  echo "==> Launching app on ${BASE_URL}"
  # No DISPLAY in headless => the show=True browser-open is a silent no-op.
  XDG_DATA_HOME="$1" uv run python -m review_app.app.entry_point \
    --port "$PORT" --host 127.0.0.1 >"${ROOT}/app.log" 2>&1 &
  APP_PID=$!
  for _ in $(seq 1 60); do
    curl -fsS "${BASE_URL}/" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  echo "!! server did not come up; see ${ROOT}/app.log" >&2
  return 1
}

# ── Phase A: first-run setup wizard (empty database) ──────────────────────────
echo "==> Phase A: setup wizard (empty database)"
WIZARD_DATA="${ROOT}/wizard-data"
mkdir -p "$WIZARD_DATA"
launch_app "$WIZARD_DATA"
# The form values are illustrative only (never submitted), so the video path
# need not exist on disk.
uv run --with selenium python scripts/screenshots/capture_wizard.py \
  --base-url "$BASE_URL" --out-dir "$OUT_DIR"
stop_app

# ── Phase B: seeded demo database ─────────────────────────────────────────────
echo "==> Phase B: seeding demo database"
DEMO_DATA="${ROOT}/demo-data"
export XDG_DATA_HOME="$DEMO_DATA"
export DEMO_VIDEO_DIR="${ROOT}/demo-videos"
# Cut the demo clips from a real camera-trap video when one is available
# (override with DEMO_SOURCE_VIDEO); otherwise seed_demo.py uses a test pattern.
export DEMO_SOURCE_VIDEO="${DEMO_SOURCE_VIDEO:-/home/jonas/Downloads/input_videos/20200511_095113_Pan troglodytes verus_273213_1325916.MP4}"
uv run python scripts/screenshots/seed_demo.py
launch_app "$DEMO_DATA"
echo "==> Capturing app screenshots"
uv run --with selenium python scripts/screenshots/capture.py \
  --base-url "$BASE_URL" --out-dir "$OUT_DIR" --annotator alice
stop_app

echo "==> Done. Screenshots in ${OUT_DIR}/"
