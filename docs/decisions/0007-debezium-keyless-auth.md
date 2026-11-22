# 0007. Debezium authenticates with the managed identity through a small Java helper

Date: 2026-11-22
Decided by: agent. The owner asked whether Kafka's built-in client assertion
support makes the helper unnecessary; this record answers that.

## Context

Debezium Server needs two Entra tokens: one for Azure SQL (reading CDC change
tables) and one for Event Hubs (publishing). Stop condition 2 forbids keys,
passwords and connection strings.

Facts (`docs/verification/2026-11-22-debezium-terraform.md`, checked 2026-11-22):
- The Debezium Event Hubs sink accepts only a connection string.
- The Debezium image does not include `azure-identity`, so the JDBC driver's
  `ActiveDirectoryManagedIdentity` mode would need extra jars.
- The JDBC driver (12.4 and later; Debezium ships 12.4.2) accepts
  `accessTokenCallbackClass`, a class that returns a token.
- Kafka clients authenticate to Event Hubs with SASL OAUTHBEARER and a login
  callback handler.
- Container Apps exposes managed identity only through the HTTP
  `IDENTITY_ENDPOINT`. No token file is projected.

## Decision

`debezium/auth` holds three JDK-only classes, packaged as one jar in
Debezium's `lib/`:
- `ManagedIdentityToken` calls `IDENTITY_ENDPOINT` for the user-assigned
  identity and caches the token.
- `SqlTokenCallback` implements the JDBC driver's token callback.
- `EventHubsTokenHandler` implements Kafka's OAUTHBEARER login callback.

Debezium uses the Kafka sink against the Event Hubs Kafka endpoint.

## Alternatives rejected

- **Event Hubs sink with a connection string.** Needs a key. Stop condition 2.
- **Kafka's built-in jwt-bearer retriever (KIP-1139, `assertion.file`).** It
  reads an assertion from a file. Container Apps does not provide one, so it
  would need a sidecar writing tokens to a shared file, plus an Entra app
  registration trusting the managed identity (a new object outside the
  resource group, stop condition 3). Whether Kafka's retriever sends the
  `client_credentials` plus `client_assertion` request Entra expects is
  unverified.
- **Adding `azure-identity` jars to Debezium.** Works for SQL only, risks
  classpath conflicts with Debezium's bundled `azure-core`, and still leaves
  Event Hubs unsolved.

## How it is verified

- `debezium/auth/test/cdcimpact/SelfTest.java` runs during the image build and
  checks URL building, response parsing and resource derivation.
- The spike proves the live path: Debezium connects to SQL and publishes
  events (spike items 1 and 3).
