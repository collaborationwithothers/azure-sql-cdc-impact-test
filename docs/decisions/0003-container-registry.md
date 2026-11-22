# 0003. Container registry in the persistent layer

Date: 2026-11-22
Decided by: agent

## Context

The test rig runs as a Container Apps job (design section 5). It needs a
custom image: the .NET load generator, collector and orchestrator, plus Debezium
Server with extra JAR files. The image must come from somewhere Container Apps
can pull it without a stored credential.

## Decision

An Azure Container Registry, Basic SKU, admin user disabled, in the persistent
resource group. The pipeline identity holds `AcrPush`. The test-rig identity
holds `AcrPull`. The pipeline builds the image on the GitHub runner with
`docker build` and pushes it.

## Alternatives rejected

- **GitHub Container Registry.** A package is not guaranteed to be public, and
  a private package would need a pull token, which is a stored credential.
- **Registry in the ephemeral layer.** The test-rig identity would need
  `AcrPull` on a registry created per run. The pipeline identity is only
  Contributor and cannot create role assignments.
- **`az acr build`.** Possible on Basic, but building on the runner keeps the
  build log in GitHub and avoids a dependency on ACR Tasks.

## Cost

The Basic registry is billed per day while it exists. It is the only
persistent resource with a noticeable idle cost. The budget alert covers it.
No price is stated here; the report reads prices from the Retail Prices API.

## How it was verified

Microsoft Learn states Basic includes Entra authentication and supports
managed-identity pull from Container Apps jobs
(`docs/verification/2026-11-22-platform.md`). Proven live in the spike.
