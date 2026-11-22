# 0006. Persistent layer applied

Date: 2026-11-22
Decided by: agent, under AGENTS.md Bootstrap.

## What happened

1. The active subscription name and ID were compared with the values the owner
   gave at the start of the session. Both matched.
2. `scripts/bootstrap.sh plan` produced 19 creates, 0 changes, 0 deletes. Every
   resource was in `rg-cdc-impact-test` or scoped to a resource inside it. The
   plan was read before apply.
3. `apply` created all 19.
4. `migrate` moved the state from a temporary local backend into the `tfstate`
   container, key `persistent.tfstate`. The local state files were deleted.
   `terraform state list` against the remote backend returned 20 entries
   (19 resources and one data source).
5. `secrets` wrote `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and
   `AZURE_SUBSCRIPTION_ID` to the `azure` environment, as repository admin.

## Why a local backend first

The storage account that holds state is created by this same layer. On the
first apply it does not exist, so a temporary `backend_override.tf` points
Terraform at a local backend. After the apply, the state moves to the account
and every later run uses the `azurerm` backend with `-backend-config`.

## Follow-up

The storage account carries `prevent_destroy`, because it holds this layer's
own state. Blob versioning and 14 day soft delete are on.

## Not yet verified

The federated credential has not been used. The first workflow sign-in proves
the OIDC subject.
