# Debezium Server with the managed identity token classes (decision 0007), the
# marker-file control script (decision 0010) and the Application Insights
# Java agent (decision 0011). Build context: the repository root.

FROM eclipse-temurin:21-jdk AS build
ARG AGENT_VERSION=3.7.10
# SHA-256 computed on 2026-11-22 from the GitHub release asset.
ARG AGENT_SHA256=93a70c8f5d364c7e777f6c4d1b235dba91aef8448bd3fa94359f1d7f3e2eb0ec
COPY --from=quay.io/debezium/server:3.7.1.Final /debezium/lib /dbzlib
COPY debezium/auth /auth
RUN javac --release 21 -d /out -cp "/dbzlib/*" /auth/src/cdcimpact/*.java \
 && javac --release 21 -d /selftest -cp "/out:/dbzlib/*" /auth/test/cdcimpact/SelfTest.java \
 && java -cp "/out:/selftest:/dbzlib/*" cdcimpact.SelfTest \
 && jar cf /cdcimpact-auth.jar -C /out .
RUN curl -sSfL -o /agent.jar "https://github.com/microsoft/ApplicationInsights-Java/releases/download/${AGENT_VERSION}/applicationinsights-agent-${AGENT_VERSION}.jar" \
 && echo "${AGENT_SHA256}  /agent.jar" | sha256sum -c -

FROM quay.io/debezium/server:3.7.1.Final
USER root
COPY --from=build /cdcimpact-auth.jar /debezium/lib/cdcimpact-auth.jar
COPY --from=build /agent.jar /opt/appinsights/agent.jar
COPY debezium/applicationinsights.template.json /opt/appinsights/applicationinsights.template.json
COPY debezium/control.sh /debezium/control.sh
RUN chmod 755 /debezium/control.sh && chown -R jboss /opt/appinsights /debezium/config /debezium/data
USER jboss
# Starts the bundled Prometheus JMX exporter; the orchestrator reads it on localhost.
ENV JMX_EXPORTER_PORT=9404
ENTRYPOINT ["/debezium/control.sh"]
