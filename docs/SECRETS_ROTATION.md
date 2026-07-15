# Secrets Rotation

## TL;DR

If a secret was leaked (e.g. in chat, in a screenshot, in a commit), rotate it at the vendor, store the new value in `.env` (gitignored), update any agent that needs it, and verify the leak source is purged. See below for the T3N-specific runbook.

## General rotation steps

- Identify the leaked secret's prefix and the vendor.
- Visit the vendor's console; revoke the old key and generate a new one.
- Store the new key in `.env` (gitignored) or a real secrets manager.
- Update any agent, script, or cron job that consumes the old key.
- Confirm the new key works, for example with a smoke call to the vendor API.
- Verify the leaked value is not present in tracked files with [`scripts/check_secrets.sh`](../scripts/check_secrets.sh).
- Document the rotation here with a one-line entry: `<service> key rotated YYYY-MM-DD by <who>`.

## T3N-specific runbook

This runbook covers [Issue #13](https://github.com/AegisFintech/AIQuantTrader/issues/13).

1. Visit the T3N console (Terminal 3 network dashboard) and revoke the leaked key (`0x0207…`).
2. Generate a new 256-bit hex bearer secret. Save it in the vendor console flow or another secure local secret handoff.
3. Add the new key to `/root/AIQuantTrader/.env` as `T3N_API_KEY=<new-value>`. The `.env` file is already gitignored.
4. Add the corresponding DID to `.env` as `T3N_DID=<the W3C DID, public-by-design>`.
5. Restart any agent that consumes the key. Currently none in this repo consume it, but agents may load it in the future.
6. Run `./scripts/check_secrets.sh` to confirm the new key is not in any tracked file.
7. Update this doc with a one-line entry: `T3N key rotated YYYY-MM-DD by Aloy`.

## What NOT to do

- Do NOT paste the new key in chat, screenshots, or commits.
- Do NOT commit the `.env` file.
- Do NOT add the new key to `.env.sample` (which is committed).
- Do NOT log the new key anywhere.

## Audit log

| Date | Event | Status |
| --- | --- | --- |
| 2026-06-11 | T3N key leaked in chat ([Issue #13](https://github.com/AegisFintech/AIQuantTrader/issues/13)). Audit confirmed key not in tracked files under `/root/AIQuantTrader`. | Rotation pending Aloy's T3N console action. |
