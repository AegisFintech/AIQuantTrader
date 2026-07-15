# XAU Parity Retest

## TL;DR

Run `scripts/xau_parity_watch.sh` to re-execute the M2.3d live XAU parity test
against whatever is currently in the warehouse. Wire it into cron for daily
checks.

## Why

Issue #22 is data-blocked, not code-blocked. The live test in
`tests/test_xau_parity_live.py` is xfailed because the warehouse does not yet
contain eligible June 11+ XAU bars for the live MT5 acknowledgements.

The watch script re-evaluates that xfailed test whenever it runs. Once the MT5
export pipeline ships the missing bars, pytest records the test as `XPASS` and
the script writes a fresh JSON report under `state/research/reports/`.

## Usage

```bash
# One-shot, against the live warehouse after harvesting MT5 exports:
./scripts/xau_parity_watch.sh --data-source mt5-export --verbose

# Or against the database directly:
./scripts/xau_parity_watch.sh

# JSON report:
#   state/research/reports/xau_parity_<timestamp>.json
```

The script exits `0` for both `XPASS` and `XFAIL`. `XFAIL` means the retest ran
successfully but the live parity test is still blocked by missing data or
insufficient matching acknowledgements. Script errors, missing warehouses, and
unexpected pytest failures exit non-zero.

## Cron Entry

Add the cron entry from the operator account when ready:

```cron
# Nightly XAU parity retest at 02:00 SGT (18:00 UTC)
0 18 * * * /root/AIQuantTrader/scripts/xau_parity_watch.sh --data-source mt5-export >>/root/AIQuantTrader/logs/xau_parity_cron.log 2>&1
```

No `state/research/cron_jobs.json` manifest exists in the current checkout.
The repository does have `config/aiquanttrader.cron`, but it is an `/etc/cron.d`
policy with a different schema, so this retest job is documented here only.
Do not add it to the host crontab until the schedule is approved operationally.

## Closing #22

Once a watch report records `live_test_status == "XPASS"` and
`match_rate >= 0.95`, run the parity test manually one more time without the
xfail decorator, or remove the xfail marker from `tests/test_xau_parity_live.py`
and confirm the assertion passes. Then close #22.

If the nightly watch runs for 30 days and stays `XFAIL`, the MT5 export data is
not arriving. Escalate to the M1.5 owner for the export pipeline.
