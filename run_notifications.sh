#!/usr/bin/env bash
# run-scripts.sh – invoked by systemd

set -euo pipefail        # fail fast & catch common mistakes

log() {
    # Emit timestamped messages to stdout; systemd captures stdout/stderr
    printf '%s %s\n' "$(date +'%Y-%m-%d %H:%M:%S%z')" "$*"
}

log "=== notification job started ==="
log "user: $(whoami)"



# Go to your project directory

log "Changing to project directory..."
PROJECT_DIR="/home/usr04/myfiles/templogger"
cd "${PROJECT_DIR}" || {
    log "Project directory not found!"
    exit 1
}
log "cwd : $(pwd)"

source "${PROJECT_DIR}/venv/bin/activate"
# Set the PYTHONPATH so Python knows where to find the modules
export PYTHONPATH=/home/usr04/myfiles/templogger/src

# ---- Run Python tasks ------------------------------------------------------

python "/home/usr04/myfiles/templogger/src/templogger/notifications.py"

log "=== notification job finished ==="
