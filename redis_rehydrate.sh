#!/bin/bash
set -euo pipefail

# Log function (optional but helpful)
log() {
    echo "[$(date +'%F %T')] $*"
}

log "Waiting for Redis container to be running..."
while ! docker container inspect -f '{{.State.Running}}' redis-stack 2>/dev/null | grep -q true; do
    log "Container not running yet... sleeping..."
    sleep 2
done

log "Waiting for Redis server inside container to be ready..."
until docker exec redis-stack redis-cli PING 2>/dev/null | grep -q PONG; do
    log "Redis not ready yet... sleeping..."
    sleep 2
done


# Flush Redis DB
log "Flushing Redis database..."
docker exec -i redis-stack redis-cli FLUSHALL

# Go to your project directory
log "Changing to project directory..."
cd /home/usr04/myfiles/templogger/ || {
    log "Project directory not found!"
    exit 1
}

# Activate virtual environment
log "Activating virtual environment..."
source venv/bin/activate

# Run the rehydration script
log "Running rehydration script..."
python /home/usr04/myfiles/templogger/src/templogger/rehydrate_redis.py

log "Rehydration completed successfully."
