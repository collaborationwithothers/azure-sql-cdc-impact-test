#!/usr/bin/env bash
# One-time bootstrap of the persistent layer. See docs/bootstrap.md.
#
# Usage:
#   EXPECTED_SUBSCRIPTION_NAME="..." EXPECTED_SUBSCRIPTION_ID="..." \
#     scripts/bootstrap.sh plan|apply|migrate|secrets
#
# The expected subscription name and ID come from the owner at the start of the
# session. They are never committed.
set -euo pipefail

REPO="collaborationwithothers/azure-sql-cdc-impact-test"
RG="rg-cdc-impact-test"
DIR="$(cd "$(dirname "$0")/../infra/persistent" && pwd)"
PLAN="$DIR/bootstrap.tfplan"

die() { echo "STOP: $*" >&2; exit 1; }

check_subscription() {
  : "${EXPECTED_SUBSCRIPTION_NAME:?set EXPECTED_SUBSCRIPTION_NAME}"
  : "${EXPECTED_SUBSCRIPTION_ID:?set EXPECTED_SUBSCRIPTION_ID}"
  local name id
  name=$(az account show --query name -o tsv)
  id=$(az account show --query id -o tsv)
  echo "Active subscription name: $name"
  [ "$name" = "$EXPECTED_SUBSCRIPTION_NAME" ] || die "active subscription name does not match"
  [ "$id" = "$EXPECTED_SUBSCRIPTION_ID" ] || die "active subscription ID does not match"
  export ARM_SUBSCRIPTION_ID="$id"
  echo "Subscription name and ID match."
}

storage_account() {
  az storage account list -g "$RG" --query "[?tags.purpose=='state-and-results'].name | [0]" -o tsv 2>/dev/null || true
}

tf_vars() {
  export TF_VAR_github_subject_prefix
  TF_VAR_github_subject_prefix=$(gh api "repos/$REPO/actions/oidc/customization/sub" --jq .sub_claim_prefix)
  [ -n "$TF_VAR_github_subject_prefix" ] || die "could not read the OIDC subject prefix"
  # Budget alerts go to the signed-in Azure user. Never committed.
  local mail
  mail=$(az ad signed-in-user show --query "mail || userPrincipalName" -o tsv)
  export TF_VAR_budget_contact_emails="[\"$mail\"]"
}

init() {
  local sa
  sa=$(storage_account)
  if [ -z "$sa" ]; then
    echo "State account does not exist yet. Using a local backend for the first apply."
    printf 'terraform {\n  backend "local" {}\n}\n' > "$DIR/backend_override.tf"
    terraform -chdir="$DIR" init -input=false -reconfigure
  else
    rm -f "$DIR/backend_override.tf"
    terraform -chdir="$DIR" init -input=false -reconfigure \
      -backend-config="storage_account_name=$sa" \
      -backend-config="resource_group_name=$RG"
  fi
}

case "${1:-}" in
  plan)
    check_subscription
    tf_vars
    init
    terraform -chdir="$DIR" plan -input=false -out="$PLAN"
    echo
    echo "Resource addresses and resource groups in the plan:"
    terraform -chdir="$DIR" show -json "$PLAN" | python3 -c '
import json, sys
p = json.load(sys.stdin)
bad = []
for rc in p.get("resource_changes", []):
    a = rc["change"]["actions"]
    after = rc["change"].get("after") or {}
    rg = after.get("resource_group_name") or after.get("name") if rc["type"] == "azurerm_resource_group" else after.get("resource_group_name")
    print(f"  {a} {rc[\"address\"]}  rg={rg}")
    if "delete" in a:
        bad.append(rc["address"] + " is deleted")
    scope = after.get("scope")
    if scope and "/resourceGroups/rg-cdc-impact-test" not in scope:
        bad.append(rc["address"] + " has a scope outside the project resource group")
    if rg not in (None, "rg-cdc-impact-test"):
        bad.append(rc["address"] + " targets another resource group")
if bad:
    print("PLAN REJECTED:"); [print("  " + b) for b in bad]; sys.exit(1)
print("Plan check passed: only this project resource group is touched, nothing is deleted.")
'
    ;;
  apply)
    check_subscription
    [ -f "$PLAN" ] || die "run plan first"
    terraform -chdir="$DIR" apply -input=false "$PLAN"
    rm -f "$PLAN"
    ;;
  migrate)
    check_subscription
    sa=$(storage_account)
    [ -n "$sa" ] || die "state account not found"
    rm -f "$DIR/backend_override.tf"
    terraform -chdir="$DIR" init -input=false -migrate-state -force-copy \
      -backend-config="storage_account_name=$sa" \
      -backend-config="resource_group_name=$RG"
    rm -f "$DIR/terraform.tfstate" "$DIR/terraform.tfstate.backup"
    echo "Persistent state now lives in the state container."
    ;;
  secrets)
    check_subscription
    client_id=$(az identity show -g "$RG" -n id-cdc-impact-pipeline --query clientId -o tsv)
    tenant_id=$(az account show --query tenantId -o tsv)
    # Values are piped, never echoed. Needs admin on the repository.
    printf '%s' "$client_id" | gh secret set AZURE_CLIENT_ID --env azure --repo "$REPO"
    printf '%s' "$tenant_id" | gh secret set AZURE_TENANT_ID --env azure --repo "$REPO"
    printf '%s' "$ARM_SUBSCRIPTION_ID" | gh secret set AZURE_SUBSCRIPTION_ID --env azure --repo "$REPO"
    gh secret list --env azure --repo "$REPO"
    ;;
  *)
    die "usage: $0 plan|apply|migrate|secrets"
    ;;
esac
