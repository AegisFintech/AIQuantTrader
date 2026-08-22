# Reactive Smart-Money Scalper V3

Status: implemented for credential-free paper and isolated shadow evaluation;
not approved for live execution or production promotion.

## Outcome and retirement boundary

`smart-money-scalper-v3` is the only active smart-money scalper implementation.
The v1 and v2 Python kernels, strategy TOML files, imports, selectors, tests,
dashboard contracts, and generated-schema enum entries are removed. Git history
and `SCALPER_V2_OVERHAUL.md` remain immutable failure evidence; they have no
runtime authority. Avellaneda-Stoikov and the independent order-flow scalper
remain because they are separate live/research strategies rather than older
versions of this challenger.

V3 cannot be selected by live execution or a deployment bundle. It is admitted
only by paper and shadow configuration. The durable operator kill remains an
independent hard stop and no LLM response can influence strategy, risk, or
orders.

## Causal decision path

```text
closed 15m direction == closed 5m direction
  -> closed 1m BOS/CHoCH/sweep OR aligned intrabar momentum
  -> SMC confluence >= 6 (>= 7 in high volatility)
  -> book + trade-flow + microprice agree
  -> at least 2 quality 30/60/120/180s forecasts agree
  -> expected move clears fees + expected slippage + safety + net edge
  -> same-side signal persists across 2 consecutive market states
  -> post-only maker entry -> synchronous risk authority -> paper simulator
```

The learner samples every five seconds while decisions remain event-driven on
the one-second market state. Each horizon has independent weights and causal
labels; no observation is trained before its fixed future horizon elapses. A
shared bounded pending-label queue and explicit model state keep checkpoints
below the paper journal's 65,536-byte strategy-memory limit.

## Execution and risk lifecycle

- Entry quantity is 0.001 BTC and halves in high volatility.
- Entry is post-only at the current best bid/ask. It rests for at most 15
  seconds and cancels sooner on strong opposite book plus trade flow.
- Attempts are separated by at least 10 seconds, capped at 96 per UTC day, and
  followed by a 15-second post-exit cooldown.
- Stops scale with ATR from 6 to 14 bps. Targets scale from 12 to 24 bps with a
  minimum 1.5 reward/risk ratio.
- Break-even and trailing protection tighten the stop without averaging down.
- A take-profit first rests post-only for two seconds, then cancels and falls
  back to a reduce-only market exit after a one-second cancel grace.
- Stop, reversal, no-progress, and hard-time exits are reduce-only taker orders.
  No-progress is 45 seconds and the unconditional cap is 120 seconds.

These strategy controls sit below the existing synchronous risk authority.
Daily loss, drawdown, position, inventory, leverage, open-order, order-size,
disconnect, stale-data, circuit-breaker, and durable operator-kill limits always
override them.

## Forecast and economic gates

Each horizon needs at least 1,000 causally resolved labels. Its most recent 128
labels must show at least 55% directional accuracy and no more than 10 bps mean
absolute error. At least two horizons must pass and predict the same direction.
The entry uses the strongest aligned quality horizon, not an ex-post selected
label horizon.

The dynamic baseline hurdle is:

```text
maker entry fee
+ 65% maker / 35% taker expected exit fee
+ 35% expected taker slippage
+ 1.0 bps safety margin
+ 1.5 bps required net edge
```

With the checked-in uncalibrated baseline scenario this is 6.725 bps, above the
static 6.5 bps floor. High volatility multiplies the result by 1.25. Costs are
not relaxed to manufacture trades.

## Decisions, alternatives, tradeoffs, and performance

| Choice | Why | Alternative rejected | Tradeoff / performance implication |
|---|---|---|---|
| Fixed multi-horizon online linear models | Causal, explainable, restartable, and cheap enough for every market state. | Screenshot/LLM direction or deep learning. | Linear models miss nonlinear structure; four 8-weight predictions are constant-time and the five-second learning cadence bounds CPU and journal size. |
| Two quality and directionally aligned horizons | Reduces one-horizon noise and horizon cherry-picking. | Use the largest prediction regardless of validation. | Fewer trades and longer warmup in exchange for stronger out-of-sample discipline. |
| Dynamic cost gate | A high hit rate is irrelevant if moves do not pay fees and slippage. | A fixed direction threshold or forced trade quota. | Explicitly abstains during weak edge and may remain inactive for long periods. |
| Maker-first entry and profit exit | Avoids paying taker fees on every short-horizon round trip. | Immediate market entry and exit. | Conservative market-by-price queues produce missed fills; the longer 15-second entry TTL increases fills but is cancelled on adverse flow. |
| 45/120-second lifecycle | Matches seconds-to-minutes scalping and caps adverse inventory time. | Prior 60/180-second lifecycle or a five-minute hold. | More taker exits and churn, but materially less stale thesis exposure. |
| Deterministic rules retain authority | Reproducible replay, typed checkpoints, and enforceable risk precedence. | Let an LLM confirm or veto orders. | Cannot use visual narrative directly; optional LLM output remains retrospective evidence only. |

## Initial retained replay evidence

An isolated replay used 400,825 verified raw Hyperliquid frames from
2026-08-21 17:32 UTC through 2026-08-22 02:32 UTC. No frames were excluded.
The shared production consumer evaluated 29,516 market states, emitted nine
risk decisions, attempted eight maker entries, and produced two maker fills: a
0.001 BTC buy at 78,046 and sell at 78,200. The completed scalp earned 19.73
bps gross, $0.154 gross, and $0.1305631 net after $0.0234369 simulated fees.
The latest aggregate diagnostic showed 5,899 resolved samples, 65.625% worst
recent directional accuracy, and 8.3532 bps worst recent MAE.

This single completed trade proves path viability only. It does not prove
profitability, fill calibration, regime robustness, or promotion eligibility.
The baseline scenario remains uncalibrated and the candidate must still pass
the immutable progression:

`research -> backtest -> walk-forward -> paper -> shadow -> human review -> canary -> production`

## Operator rollout

After merge, rebuild the paper image and start the paper plus monitoring profile
using `PAPER_TRADING_RUNBOOK.md`. A strategy identity change intentionally
starts a new journal run. Do not delete prior journals or raw market data, and
do not clear the existing operator kill without an explicit operator decision.
Use Grafana's per-horizon prediction, quality, MAE, economic-hurdle, exact gate,
fill, PnL, and host-health panels to distinguish selectivity from a failed feed.
