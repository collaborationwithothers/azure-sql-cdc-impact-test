#!/usr/bin/env bash
# Exercises debezium/control.sh inside the real Debezium image. No database:
# Debezium is expected to start and then fail to connect, which must produce
# the "failed" marker; "done" must make the container exit 0.
set -euo pipefail
IMAGE="$1"
PLATFORM="${2:-linux/amd64}"
SHARED="$(mktemp -d)"
NAME="cdcrig-dbz-$$"
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true; rm -rf "$SHARED"' EXIT
mkdir -p "$SHARED/debezium"
chmod -R 777 "$SHARED"
date +%s > "$SHARED/heartbeat"

docker run -d --platform "$PLATFORM" --name "$NAME" -v "$SHARED:/shared" "$IMAGE" >/dev/null

wait_for() {
  for _ in $(seq 1 "$2"); do [ -e "$1" ] && return 0; sleep 1; done
  echo "FAIL: $1 did not appear"; docker logs "$NAME" | tail -20; exit 1
}

printf 'debezium.sink.type=kafka\ndebezium.source.connector.class=io.debezium.connector.sqlserver.SqlServerConnector\ndebezium.source.database.hostname=nowhere.invalid\n' \
  > "$SHARED/debezium/application.properties"
touch "$SHARED/debezium/start"
wait_for "$SHARED/debezium/running" 30
echo "ok - running marker"
wait_for "$SHARED/debezium/failed" 180
echo "ok - failed marker when Debezium exits on its own"
grep -q "Debezium" "$SHARED/debezium/debezium.log" && echo "ok - debezium.log written"

touch "$SHARED/done"
for _ in $(seq 1 30); do
  [ "$(docker inspect -f '{{.State.Running}}' "$NAME")" = "false" ] && break
  sleep 1
done
code=$(docker inspect -f '{{.State.ExitCode}}' "$NAME")
[ "$code" = "0" ] || { echo "FAIL: exit code $code"; exit 1; }
echo "ok - done marker makes the container exit 0"
