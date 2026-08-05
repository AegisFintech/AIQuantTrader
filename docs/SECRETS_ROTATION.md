# Secrets Rotation

## TL;DR

If a secret was leaked (for example in chat, a screenshot, or a commit), rotate
it at the vendor, store the replacement in a secrets manager or a mode-`0600`
file outside the repository, update the owning service, and verify the leak
source is purged. Runtime containers mount private keys below `/run/secrets`.

## General rotation steps

- Identify the leaked secret's prefix and the vendor.
- Visit the vendor's console; revoke the old key and generate a new one.
- Store the new key in a secrets manager or a mode-`0600` file outside the
  repository.
- Update any agent, script, or cron job that consumes the old key.
- Confirm the new key works, for example with a smoke call to the vendor API.
- Verify the leaked value is not present in tracked files with [`scripts/check_secrets.sh`](../scripts/check_secrets.sh).
- Document the rotation here with a one-line entry: `<service> key rotated YYYY-MM-DD by <who>`.

## T3N-specific runbook

This runbook covers [Issue #13](https://github.com/AegisFintech/AIQuantTrader/issues/13).

1. Visit the T3N console (Terminal 3 network dashboard) and revoke the leaked key (`0x0207…`).
2. Generate a new 256-bit hex bearer secret. Save it in the vendor console flow or another secure local secret handoff.
3. Store the new key outside the repository in the owner's secrets manager.
4. Store the corresponding public DID with the consumer's deployment metadata.
5. Restart any agent that consumes the key. Currently none in this repo consume it, but agents may load it in the future.
6. Run `./scripts/check_secrets.sh` to confirm the new key is not in any tracked file.
7. Update this doc with a one-line entry: `T3N key rotated YYYY-MM-DD by Aloy`.

## What NOT to do

- Do NOT paste the new key in chat, screenshots, or commits.
- Do NOT store private keys in repository-local environment files.
- Do NOT log the new key anywhere.

## Audit log

| Date | Event | Status |
| --- | --- | --- |
| 2026-06-11 | T3N key leaked in chat ([Issue #13](https://github.com/AegisFintech/AIQuantTrader/issues/13)). Audit confirmed key not in tracked files under `/root/AIQuantTrader`. | Rotation pending Aloy's T3N console action. |
