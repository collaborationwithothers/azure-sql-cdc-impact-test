# Test-rig image: .NET orchestrator, load generator and collector, plus
# Debezium Server with the managed identity token classes (decision 0007).
# Debezium runs as a child process of the orchestrator (decision 0009).

FROM quay.io/debezium/server:3.7.1.Final AS debezium

# Compile and self-test the token classes against Debezium's own jars.
FROM eclipse-temurin:21-jdk AS java
COPY --from=debezium /debezium/lib /dbzlib
COPY debezium/auth /auth
RUN javac --release 21 -d /out -cp "/dbzlib/*" /auth/src/cdcimpact/*.java \
 && javac --release 21 -d /selftest -cp "/out:/dbzlib/*" /auth/test/cdcimpact/SelfTest.java \
 && java -cp "/out:/selftest:/dbzlib/*" cdcimpact.SelfTest \
 && jar cf /cdcimpact-auth.jar -C /out .

FROM mcr.microsoft.com/dotnet/sdk:10.0 AS dotnet
WORKDIR /src
COPY nuget.config global.json Directory.Build.props Directory.Packages.props ./
COPY sql ./sql
COPY src ./src
RUN dotnet publish src/orchestrator/Orchestrator.csproj -c Release -o /app

FROM mcr.microsoft.com/dotnet/runtime:10.0
COPY --from=eclipse-temurin:21-jre /opt/java/openjdk /opt/java/openjdk
ENV JAVA_HOME=/opt/java/openjdk \
    PATH=/opt/java/openjdk/bin:$PATH \
    OTEL_SDK_DISABLED=true
COPY --from=debezium /debezium /debezium
COPY --from=java /cdcimpact-auth.jar /debezium/lib/cdcimpact-auth.jar
COPY --from=dotnet /app /app
RUN chown -R app:app /debezium && mkdir -p /tmp/results && chown app:app /tmp/results
USER app
WORKDIR /app
ENTRYPOINT ["dotnet", "/app/rig.dll"]
