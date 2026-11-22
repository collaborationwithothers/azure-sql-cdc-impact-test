#!/usr/bin/env bash
# Builds the rig and Debezium images and runs the container tests:
# 1. Python unit tests and SQL tests inside the rig image, against SQL Server.
# 2. The Debezium control protocol (start, running, failed, stop, done).
# Used by CI and locally. Needs Docker.
set -euo pipefail
cd "$(dirname "$0")/../.."

PLATFORM="${PLATFORM:-linux/amd64}"
NET="cdcrig-test-$$"
SQL="cdcrig-sql-$$"
# Throwaway password for a throwaway local container; generated per run, never stored.
PASSWORD="Aa1-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"

cleanup() {
  docker rm -f "$SQL" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker build --platform "$PLATFORM" -t cdc-impact-rig:test -f Dockerfile .
docker build --platform "$PLATFORM" -t cdc-impact-debezium:test -f debezium/Dockerfile .

docker network create "$NET" >/dev/null
docker run -d --platform linux/amd64 --name "$SQL" --network "$NET" \
  -e ACCEPT_EULA=Y -e MSSQL_AGENT_ENABLED=true -e "MSSQL_SA_PASSWORD=$PASSWORD" \
  mcr.microsoft.com/mssql/server:2022-latest >/dev/null

echo "waiting for SQL Server"
for _ in $(seq 1 60); do
  if docker exec "$SQL" /opt/mssql-tools18/bin/sqlcmd -C -S localhost -U sa -P "$PASSWORD" -Q "SELECT 1" >/dev/null 2>&1; then break; fi
  sleep 2
done

ODBC="Driver={ODBC Driver 18 for SQL Server};Server=tcp:$SQL,1433;Database={db};UID=sa;PWD=$PASSWORD;TrustServerCertificate=yes;Encrypt=yes"
docker run --rm --platform "$PLATFORM" --network "$NET" -e "RIG_TEST_ODBC=$ODBC" -v "$PWD:/repo:ro" \
  --entrypoint python cdc-impact-rig:test -m pytest -q -p no:cacheprovider /repo/tests/rig

echo "Debezium control protocol"
tests/rig/test-debezium-control.sh cdc-impact-debezium:test "$PLATFORM"
