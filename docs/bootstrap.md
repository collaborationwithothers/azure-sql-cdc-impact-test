# Bootstrap: the persistent layer

This repo runs an automated experiment on Azure. Most Azure resources exist only
during a test run and are created and destroyed by GitHub Actions (the
"ephemeral layer"). A small set of resources is created once and kept (the
"persistent layer"). This page explains how the persistent layer is created.

## What the persistent layer contains

All of it lives in one resource group, `rg-cdc-impact-test`, in Sweden Central.

| Resource | Why it exists |
| --- | --- |
| Storage account, containers `tfstate` and `results` | Terraform state for both layers, and raw results written by the test rig. Shared key access is off. |
| Log Analytics workspace | Pool and database metrics must outlive the pool. |
| Container registry (Basic) | Holds the test-rig image. See `docs/decisions/0003-container-registry.md`. |
| Pipeline managed identity | GitHub Actions signs in as this identity through OpenID Connect federation. It is Contributor on this resource group only. |
| Test-rig managed identity | The test-rig container runs as this identity. It becomes the Entra admin of each ephemeral SQL server. |
| Role assignments | Every data-plane role either identity needs. The pipeline identity cannot create role assignments itself. |
| Budget alert | Must exist before the first ephemeral apply. |

OpenID Connect (OIDC) federation means GitHub issues a short-lived signed token
to a workflow job, and Microsoft Entra ID exchanges it for an Azure token. No
password or key is stored anywhere. The federated credential only accepts
tokens from jobs that run in this repository's `azure` GitHub environment, and
that environment only accepts deployments from `main`.

## Who can run it

- Owner on the subscription, because it creates role assignments.
- Admin on the GitHub repository, because the last step writes secrets to the
  `azure` environment. GitHub's docs say configuring an environment in an
  organization repository needs admin access. The exact level for environment
  secrets is not stated, so this repo treats admin as required. See
  `docs/verification/2026-11-22-platform.md`.

## Steps

The owner names the subscription at the start of the session. Its name and ID
are passed as environment variables and are never committed.

```bash
export EXPECTED_SUBSCRIPTION_NAME="<name given by the owner>"
export EXPECTED_SUBSCRIPTION_ID="<id given by the owner>"

scripts/bootstrap.sh plan      # checks subscription, plans, rejects any change outside the resource group
# read the plan output
scripts/bootstrap.sh apply     # applies the saved plan against a local backend
scripts/bootstrap.sh migrate   # moves the state into the tfstate container
scripts/bootstrap.sh secrets   # writes AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID to the azure environment
```

Inputs read at run time, never committed:
- The repository's OIDC subject prefix, from
  `GET /repos/{owner}/{repo}/actions/oidc/customization/sub`. This repository
  uses GitHub's immutable subject format, which contains the owner and
  repository IDs.
- The budget alert email, taken from the signed-in Azure user.

## Re-running

The same commands work later. `plan` detects that the state account exists and
initialises against it. Any change to the persistent layer goes through a pull
request first, then a local `plan` and `apply`.

## Environment secrets the pipeline needs

| Secret | Value |
| --- | --- |
| `AZURE_CLIENT_ID` | Client ID of the pipeline identity |
| `AZURE_TENANT_ID` | Tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Subscription ID |

They are environment secrets, not repository secrets, so only jobs in the
`azure` environment can read them, and GitHub masks them in logs.
