# Rig image: the Python orchestrator and collector (src/rig), with the .NET
# load generator (src/loadgen) published self-contained. Debezium runs in its
# own image (debezium/Dockerfile). Decision 0010.

FROM mcr.microsoft.com/dotnet/sdk:10.0 AS loadgen
WORKDIR /src
COPY nuget.config global.json Directory.Build.props Directory.Packages.props ./
COPY src/loadgen ./src/loadgen
RUN dotnet publish src/loadgen/LoadGen.csproj -c Release -r linux-x64 --self-contained true -o /out

FROM python:3.13-slim-bookworm
# Microsoft ODBC Driver 18 for SQL Server, from Microsoft's Debian 12 repository.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gnupg ca-certificates libicu72 \
 && curl -sSfL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
 && curl -sSfL https://packages.microsoft.com/config/debian/12/prod.list -o /etc/apt/sources.list.d/mssql-release.list \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc \
 && apt-get purge -y gnupg && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY --from=loadgen /out /opt/loadgen
COPY src/rig /src/rig
COPY sql /src/rig/cdcrig/sql
RUN pip install --no-cache-dir "/src/rig[test]"
COPY tests/rig /tests/rig
ENV PYTHONUNBUFFERED=1
WORKDIR /tmp
ENTRYPOINT ["python", "-m", "cdcrig"]
