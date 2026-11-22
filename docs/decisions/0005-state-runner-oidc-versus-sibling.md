# 0005. State storage, runner and OIDC: what is reused from the sibling repo

Date: 2026-11-22
Decided by: agent, after the owner asked for the sibling repository
`eventhubs-kafka-dotnet` to be reviewed.

## Context

`eventhubs-kafka-dotnet` is another experiment in the same organization and
subscription. It:
- Keeps Terraform state in an existing storage account outside its own
  resource group, with private access and shared keys off. Its backend
  configuration is supplied at init time.
- Runs Azure workflows on a self-hosted GitHub runner with Azure private
  networking, in a runner virtual network not managed by that repo.
- Uses an identity and federated credentials created by the owner outside the
  repo. Workflows set `ARM_USE_OIDC=true` and read `AZURE_CLIENT_ID`,
  `AZURE_TENANT_ID` and `AZURE_SUBSCRIPTION_ID` from environment secrets.

## Decision

Reuse the conventions, not the shared resources.

Reused:
- Secret names `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`.
- `ARM_USE_OIDC=true` for Terraform in workflows.
- Partial backend configuration with `use_azuread_auth = true`, values given
  at `terraform init`.

Not reused:
- **The external state account.** Using it would modify something outside this
  project's resource group (stop condition 3 in AGENTS.md). Its private access
  would also be unreachable from `ubuntu-latest`. Design section 5 puts state in
  this project's persistent layer.
- **The self-hosted runner.** AGENTS.md requires `ubuntu-latest`. Nothing on
  the runner talks to SQL: the test rig runs inside the ephemeral virtual
  network, so a private runner is not needed.
- **An externally created identity.** The design and AGENTS.md Bootstrap put
  the pipeline identity and its federated credential in the persistent layer.

## Alternatives rejected

Adopting the sibling's shared state account and runner. It would remove one
storage account, but it breaks two AGENTS.md rules and ties this repo's
lifecycle to resources it does not own. The owner can reopen this.

## How it was verified

Read `infra/*/backend.tf`, `.github/workflows/scenario.yml`,
`.github/workflows/sweeper.yml` and `docs/blueprint.md` in the sibling
repository on 2026-11-22.
