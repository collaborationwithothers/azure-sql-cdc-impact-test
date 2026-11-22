# 0002. Repository controls in place of push protection

Date: 2026-11-22
Decided by: agent

## Context

GitHub secret scanning and push protection are disabled on this repository by
an enterprise policy and cannot be turned on. The repository is public. No
human reviews pull requests (decision 0001). A committed secret or Azure
identifier would be public immediately.

## Decision

The `validate` workflow runs on every pull request and on `main`, and fails on:

1. Any gitleaks finding across the full git history. gitleaks is a secret
   scanner that matches known credential formats, including Azure storage keys,
   SAS tokens and connection strings. The binary version and its SHA-256
   checksum are pinned in the workflow.
2. Any GUID in a tracked file, via `scripts/check-identifiers.sh`.
   Subscription, tenant, client and object IDs are all GUIDs. A line that
   genuinely needs one must carry the marker `allow-guid`.

Third-party actions are pinned to commit hashes, with the tag in a comment.

## Alternatives rejected

- **The gitleaks GitHub Action.** It needs a licence key for organization
  repositories, which would itself be a secret. Running the binary avoids that.
- **Scanning only the diff.** A full-history scan also catches a secret that
  was committed and then deleted in a later commit, which is still public.

## Limitations

This is a check after the push, not a block before it. A secret pushed to a
branch is public until it is removed and rotated. It does not replace push
protection. It only makes a leak visible and stops it reaching `main`.

## How it was verified

Ran `gitleaks git` and `scripts/check-identifiers.sh` locally on 2026-11-22
with no findings. The workflow ran green on the first pull request.
