#!/usr/bin/env bash
# Fails if any tracked file contains a GUID. Subscription IDs, tenant IDs,
# client IDs and object IDs are all GUIDs, and none may be committed.
# A line that legitimately needs a GUID (for example a well-known role
# definition ID) must carry the marker "allow-guid" on the same line.
set -euo pipefail

pattern='[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'

hits=$(git ls-files -z | xargs -0 grep -nIE "$pattern" -- 2>/dev/null | grep -v 'allow-guid' || true)

if [ -n "$hits" ]; then
  echo "GUIDs found in tracked files. Remove them or mark the line allow-guid:"
  # Print the location only, not the value.
  echo "$hits" | cut -d: -f1,2
  exit 1
fi
echo "No GUIDs in tracked files."
