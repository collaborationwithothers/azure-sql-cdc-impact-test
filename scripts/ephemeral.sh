#!/usr/bin/env bash
# Shared helpers for the workflows that touch the ephemeral layer.
# Run only inside GitHub Actions, in the "azure" environment, after azure/login.
#
# Usage: scripts/ephemeral.sh discover|init|destroy|verify-clean|expired
set -euo pipefail

RG="rg-cdc-impact-test"
TF_DIR="infra/ephemeral"

mask() { [ -n "${GITHUB_ACTIONS:-}" ] && echo "::add-mask::$1"; return 0; }

# Finds persistent resources by their purpose tag and writes their names to
# $GITHUB_ENV, masked, so no generated name is printed in a public log.
discover() {
  local sa acr law rig rig_client
  sa=$(az resource list -g "$RG" --query "[?tags.purpose=='state-and-results'].name | [0]" -o tsv)
  acr=$(az resource list -g "$RG" --query "[?tags.purpose=='images'].name | [0]" -o tsv)
  law=$(az resource list -g "$RG" --query "[?tags.purpose=='diagnostics'].name | [0]" -o tsv)
  rig=$(az resource list -g "$RG" --query "[?tags.purpose=='rig-identity'].name | [0]" -o tsv)
  appi=$(az resource list -g "$RG" --query "[?tags.purpose=='telemetry'].name | [0]" -o tsv)
  [ -n "$sa" ] && [ -n "$acr" ] && [ -n "$law" ] && [ -n "$rig" ] || { echo "Persistent layer not found"; exit 1; }
  rig_client=$(az identity show -g "$RG" -n "$rig" --query clientId -o tsv)
  for v in "$sa" "$acr" "$law" "$rig_client" "$appi"; do mask "$v"; done
  {
    echo "STATE_ACCOUNT=$sa"
    echo "TF_VAR_storage_account_name=$sa"
    echo "TF_VAR_acr_name=$acr"
    echo "TF_VAR_log_analytics_workspace_name=$law"
    echo "TF_VAR_rig_identity_name=$rig"
    echo "TF_VAR_application_insights_name=$appi"
    echo "ACR_LOGIN_SERVER=$acr.azurecr.io"
    echo "RIG_CLIENT_ID=$rig_client"
  } >> "${GITHUB_ENV:-/dev/stdout}"
  echo "Persistent layer discovered."
}

init() {
  terraform -chdir="$TF_DIR" init -input=false -reconfigure \
    -backend-config="storage_account_name=${STATE_ACCOUNT:?run discover first}" \
    -backend-config="resource_group_name=$RG"
}

has_layer() { ls "$TF_DIR"/*.tf >/dev/null 2>&1; }

destroy() {
  if has_layer; then
    init
    terraform -chdir="$TF_DIR" destroy -input=false -auto-approve -lock-timeout=30m
  else
    echo "No ephemeral Terraform in this commit. Skipping terraform destroy."
  fi
  # Belt and braces: delete anything still tagged as ephemeral. Persistent
  # resources carry layer=persistent and are never matched.
  local ids
  ids=$(az resource list -g "$RG" --query "[?tags.layer=='ephemeral'].id" -o tsv)
  if [ -n "$ids" ]; then
    echo "Deleting $(echo "$ids" | wc -l) leftover ephemeral resources."
    # Several passes, because some resources depend on others.
    for _ in 1 2 3; do
      ids=$(az resource list -g "$RG" --query "[?tags.layer=='ephemeral'].id" -o tsv)
      [ -z "$ids" ] && break
      echo "$ids" | xargs -r -n 20 az resource delete --ids >/dev/null 2>&1 || true
    done
  fi
}

# Fails unless every resource in the group is tagged layer=persistent.
# Untagged resources (for example a private endpoint NIC) also fail the check.
verify_clean() {
  local left
  left=$(az resource list -g "$RG" --query "[?tags.layer!='persistent'].type" -o tsv)
  if [ -n "$left" ]; then
    echo "NOT CLEAN. Non-persistent resources remain, by type:"
    echo "$left" | sort | uniq -c
    exit 1
  fi
  echo "Clean: only persistent resources remain in the resource group."
}

# Prints "yes" if any ephemeral resource is past its deadline tag.
expired() {
  local now
  now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  az resource list -g "$RG" --query "[?tags.layer=='ephemeral'].tags.deadline" -o tsv |
    awk -v now="$now" '$0 != "" && $0 < now { found=1 } END { print (found ? "yes" : "no") }'
}

# Prints "yes" if any ephemeral resource exists at all.
exists() {
  local n
  n=$(az resource list -g "$RG" --query "length([?tags.layer!='persistent'])" -o tsv)
  [ "$n" -gt 0 ] && echo yes || echo no
}

case "${1:-}" in
  discover) discover ;;
  init) init ;;
  destroy) destroy ;;
  verify-clean) verify_clean ;;
  expired) expired ;;
  exists) exists ;;
  *) echo "usage: $0 discover|init|destroy|verify-clean|expired|exists" >&2; exit 2 ;;
esac
