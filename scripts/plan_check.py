"""Rejects a Terraform plan that deletes anything or touches another resource group.

Usage: terraform show -json PLAN | python3 scripts/plan_check.py RESOURCE_GROUP [--allow-delete]
"""
import json
import sys


def check(plan: dict, rg: str, allow_delete: bool) -> list[str]:
    problems = []
    marker = f"/resourceGroups/{rg}".lower()
    for rc in plan.get("resource_changes", []):
        actions = rc["change"]["actions"]
        if actions == ["no-op"] or actions == ["read"]:
            continue
        after = rc["change"].get("after") or {}
        address = rc["address"]
        if rc["type"] == "azurerm_resource_group":
            target_rg = after.get("name")
        else:
            target_rg = after.get("resource_group_name")
        print(f"  {actions} {address} rg={target_rg}")
        if "delete" in actions and not allow_delete:
            problems.append(f"{address} is deleted")
        if target_rg not in (None, rg):
            problems.append(f"{address} targets resource group {target_rg}")
        for key in ("scope", "target_resource_id", "resource_group_id"):
            value = after.get(key)
            if isinstance(value, str) and value.startswith("/subscriptions/") and marker not in value.lower():
                problems.append(f"{address} has {key} outside the project resource group")
    return problems


def main() -> int:
    rg = sys.argv[1]
    allow_delete = "--allow-delete" in sys.argv
    problems = check(json.load(sys.stdin), rg, allow_delete)
    if problems:
        print("PLAN REJECTED:")
        for p in problems:
            print("  " + p)
        return 1
    print("Plan check passed: only the project resource group is touched" + ("" if allow_delete else ", nothing is deleted") + ".")
    return 0


if __name__ == "__main__":
    sys.exit(main())
