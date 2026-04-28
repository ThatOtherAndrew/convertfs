#!/usr/bin/env bash
# Install convertfs as a systemd --user service so it auto-mounts at login.
#
# Usage: ./scripts/install-service.sh [mount_dir]
#   mount_dir defaults to ~/convert

set -euo pipefail

MOUNT_DIR="${1:-$HOME/convert}"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_SRC="$PROJECT_DIR/scripts/convertfs.service"
UNIT_DST="$HOME/.config/systemd/user/convertfs.service"

require() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "missing required command: $1" >&2
        exit 1
    }
}

require systemctl
require fusermount3
require pkg-config

if ! command -v uv >/dev/null 2>&1; then
    echo "uv not found. Install it from https://astral.sh/uv (or run: curl -LsSf https://astral.sh/uv/install.sh | sh)" >&2
    exit 1
fi

if ! pkg-config --exists fuse3; then
    echo "libfuse3 development headers not found (pyfuse3 will fail to build)." >&2
    echo "On Ubuntu/Debian: sudo apt install libfuse3-dev" >&2
    exit 1
fi

echo "==> installing dependencies into $PROJECT_DIR/.venv"
(cd "$PROJECT_DIR" && uv sync)

echo "==> creating mountpoint $MOUNT_DIR"
mkdir -p "$MOUNT_DIR"

echo "==> writing $UNIT_DST"
mkdir -p "$(dirname "$UNIT_DST")"
# Substitute the project dir and mount dir into the unit. We keep %h for the
# home dir so the unit stays portable if you later mv the repo.
sed \
    -e "s|%h/Dev/convertfs|${PROJECT_DIR/#$HOME/%h}|g" \
    -e "s|%h/convert|${MOUNT_DIR/#$HOME/%h}|g" \
    "$UNIT_SRC" > "$UNIT_DST"

echo "==> enabling and starting the service"
systemctl --user daemon-reload
systemctl --user enable --now convertfs.service

echo "==> done. Mounted at: $MOUNT_DIR"
echo
echo "Useful commands:"
echo "  systemctl --user status convertfs"
echo "  journalctl --user -u convertfs -f"
echo "  systemctl --user restart convertfs"
echo "  systemctl --user disable --now convertfs    # stop autostart"
echo
echo "To keep convertfs mounted while logged out, also run:"
echo "  sudo loginctl enable-linger \"$USER\""
