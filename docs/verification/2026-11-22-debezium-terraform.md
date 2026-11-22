# Verification: Debezium, JDBC driver, Terraform provider, actions

Checked: 2026-11-22 against project documentation, source at release tags,
Maven Central and the Terraform Registry.

## Debezium Server

Sources: https://debezium.io/documentation/reference/3.7/operations/debezium-server.html,
https://debezium.io/documentation/reference/3.7/connectors/sqlserver.html,
https://github.com/debezium/debezium-server/tree/v3.7.1.Final

- Latest stable: 3.7.1.Final. Image `quay.io/debezium/server:3.7.1.Final`,
  Java 21. VERIFIED.
- Event Hubs sink authenticates with a connection string only. No managed
  identity option (open issue debezium/dbz#2647). VERIFIED. Using it would need
  a key, which is stop condition 2. Not used.
- Kafka sink passes `debezium.sink.kafka.producer.*` to the Kafka producer, and
  the Debezium docs show SASL OAUTHBEARER with a custom
  `sasl.login.callback.handler.class`. VERIFIED. Bundled `kafka-clients` 4.3.1.
- Microsoft's managed identity sample for Event Hubs Kafka uses a legacy
  library and no user-assigned client ID. It is a pattern, not a drop-in.
- SQL Server connector supports SQL Server on Azure. VERIFIED.
  `driver.*` properties pass to the JDBC driver; `driver.authentication`
  accepts `ActiveDirectoryManagedIdentity`; `database.password` is optional
  with Entra authentication. VERIFIED.
- `snapshot.mode=no_data` is current; `schema_only` is deprecated. VERIFIED.
- Schema history `io.debezium.storage.file.history.FileSchemaHistory` with
  `...file.filename`; offsets `FileOffsetBackingStore` with
  `offset.storage.file.filename`. VERIFIED.
- Metrics are JMX only. `MilliSecondsBehindSource` exists. Setting
  `JMX_EXPORTER_PORT` starts the bundled Prometheus JMX exporter agent.
  VERIFIED.
- `poll.interval.ms` default 500. VERIFIED.
- Debezium checks SQL Server Agent status through `sys.dm_server_services`,
  overridable with `database.sqlserver.agent.status.query`. Behaviour on Azure
  SQL Database: UNVERIFIABLE. Spike tests it.

## Microsoft JDBC driver

Source: https://learn.microsoft.com/sql/connect/jdbc/connecting-using-azure-active-directory-authentication

- `authentication=ActiveDirectoryManagedIdentity` with `msiClientId`, or the
  client ID in `user` from 12.2, selects a user-assigned identity. VERIFIED.
- Debezium Server 3.7.1 ships `mssql-jdbc-12.4.2.jre8` and does not ship
  `azure-identity`, `msal4j` or `azure-core-http-netty`. VERIFIED by listing
  the distribution. A derived image must add them.
- Container Apps is not in the driver's list of tested managed identity hosts.
  UNVERIFIABLE. Spike item 1 tests it.

## Terraform azurerm provider

Sources: https://registry.terraform.io/providers/hashicorp/azurerm,
https://github.com/hashicorp/terraform-provider-azurerm/releases

- Latest is 5.14.0 (2026-11-13). Latest 4.x is 4.81.0. VERIFIED.
- Elastic pool resource is `azurerm_mssql_elasticpool`. VERIFIED.
- `azurerm_federated_identity_credential` uses `user_assigned_identity_id`
  from late 4.x; `parent_id` is removed in 5.0. VERIFIED.
- In 5.x, `azurerm_container_app_environment` needs
  `logs_destination = "log-analytics"`, and `azurerm_monitor_diagnostic_setting`
  uses `enabled_metric`. VERIFIED.
- Arguments used by this repo exist in both 4.81.0 and 5.14.0. VERIFIED.

## Pinned actions

Commit SHAs resolved from the GitHub API on 2026-11-22 and pinned in
workflows: actions/checkout v7.0.1, hashicorp/setup-terraform v4.0.1,
azure/login v3.1.0, actions/setup-dotnet v6.1.0, actions/setup-python v7.1.0,
actions/upload-artifact v7.0.2, actions/download-artifact v8.0.2. gitleaks
binary 8.30.1, SHA-256 from the release checksums file.
