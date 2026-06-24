#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Tunables: pass through to the Python driver. Defaults are sensible for
# a full 50-item run; override on the command line as needed.
exec python3 scripts/fire_claude_step_50.py "$@"
