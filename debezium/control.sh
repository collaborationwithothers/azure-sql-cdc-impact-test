#!/bin/bash
# Runs Debezium Server on request from the orchestrator in the sibling
# container (decision 0010). Protocol, all under $SHARED (default /shared):
#   debezium/start      -> start Debezium with debezium/application.properties, write debezium/running
#   debezium/stop       -> SIGTERM Debezium, wait, write debezium/stopped
#   done                -> stop Debezium if running and exit 0, so the job can complete
#   heartbeat           -> refreshed by the orchestrator; older than 10 minutes means it died: exit 3
# If Debezium exits on its own, debezium/failed is written.
set -u
SHARED="${SHARED:-/shared}"
D="$SHARED/debezium"
PID=""

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) control: $*"; }

stop_debezium() {
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    kill -TERM "$PID"
    for _ in $(seq 1 90); do kill -0 "$PID" 2>/dev/null || break; sleep 1; done
    kill -KILL "$PID" 2>/dev/null || true
  fi
  PID=""
}

configure_agent() {
  # Application Insights Java agent (decision 0011), only when a connection string is set.
  if [ -n "${APPLICATIONINSIGHTS_CONNECTION_STRING:-}" ] && [ -f /opt/appinsights/agent.jar ]; then
    sed "s/__CLIENT_ID__/${AZURE_CLIENT_ID:-}/" /opt/appinsights/applicationinsights.template.json > /opt/appinsights/applicationinsights.json
    export JAVA_OPTS="${JAVA_OPTS:-} -javaagent:/opt/appinsights/agent.jar"
    log "application insights agent enabled"
  fi
}

trap 'stop_debezium; exit 143' TERM INT
configure_agent
log "waiting for instructions in $SHARED"

while true; do
  if [ -f "$SHARED/done" ]; then
    stop_debezium
    log "done"
    exit 0
  fi
  if [ -f "$SHARED/heartbeat" ]; then
    age=$(( $(date +%s) - $(stat -c %Y "$SHARED/heartbeat") ))
    if [ "$age" -gt 600 ]; then
      log "orchestrator heartbeat is ${age} s old; exiting"
      stop_debezium
      exit 3
    fi
  elif [ "$SECONDS" -gt 3600 ]; then
    log "no orchestrator heartbeat after an hour; exiting"
    exit 4
  fi
  if [ -f "$D/start" ] && [ -z "$PID" ]; then
    rm -f "$D/start" "$D/stopped" "$D/failed"
    rm -rf /debezium/data/*
    cp "$D/application.properties" /debezium/config/application.properties
    (cd /debezium && exec ./run.sh) >> "$D/debezium.log" 2>&1 &
    PID=$!
    touch "$D/running"
    log "started Debezium, pid $PID"
  fi
  if [ -n "$PID" ] && ! kill -0 "$PID" 2>/dev/null; then
    log "Debezium exited on its own"
    echo "Debezium exited on its own" > "$D/failed"
    rm -f "$D/running"
    PID=""
  fi
  if [ -f "$D/stop" ]; then
    rm -f "$D/stop"
    stop_debezium
    rm -f "$D/running"
    touch "$D/stopped"
    log "stopped"
  fi
  sleep 1
done
