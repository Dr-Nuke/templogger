# run-scripts.sh – invoked by systemd

set -euo pipefail        # fail fast & catch common mistakes

log() {
    # Emit timestamped messages to stdout; systemd captures stdout/stderr
    printf '%s %s\n' "$(date +'%Y-%m-%d %H:%M:%S%z')" "$*"
}

log "=== job started ==="
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

# ---- Run Python tasks ------------------------------------------------------
python "${PROJECT_DIR}/src/templogger/co2.py"
python "/home/usr04/myfiles/templogger/src/templogger/collector.py"
python "${PROJECT_DIR}/src/templogger/derived.py"
python "${PROJECT_DIR}/src/templogger/aggregate.py"

log "success: all scripts completed without error"
log "=== job finished ==="