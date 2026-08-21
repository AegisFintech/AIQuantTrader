# ADR 0018: Embed server telemetry in the BTC paper command center

## Status

Accepted.

## Context

The BTC paper command center is Grafana's configured home dashboard, while host
resource telemetry previously appeared only on the separate platform-health
dashboard. An operator could see trading state without immediately seeing
whether the host, monitoring path, memory, disk, or network was degraded.
The word "live" also needed to distinguish a current Hyperliquid public feed
from real-money execution.

## Decision

Keep Node Exporter as the single host collector and Prometheus as the single
query source. Add an explicit `LIVE DATA / PAPER ONLY` status panel to the BTC
command center and embed a server section below the trading panels. The section
shows process and scrape heartbeats, CPU usage and logical-core capacity,
memory, root filesystem capacity, disk I/O, host network ingress/egress,
uptime, and load.

The status wording is part of the safety contract: a green service or feed
heartbeat means reachable and current, not execution-authorized. The operator
kill and paper-only mode remain visible independently.

## Alternatives considered

- Keep only a link to the platform-health dashboard. This avoids duplicate
  panels but makes the most important host failure signals one navigation step
  away from the trading decision view.
- Install another host agent such as Telegraf or a container-specific collector.
  This could add process-level detail but duplicates the existing collector,
  increases host and storage overhead, and creates another failure surface.
- Merge every platform-health panel and alert into one dashboard. This would
  reduce navigation further but make the trading view noisy and harder to scan.

## Tradeoffs

- A small set of PromQL expressions is intentionally duplicated between the
  paper and platform-health dashboards.
- Node Exporter reports host-level rather than per-container bandwidth and CPU.
  That is sufficient for server health; container attribution remains outside
  this dashboard's scope.
- The command center is taller, but the server panels are grouped below all
  trading and feed panels so the primary decision workflow is unchanged.

## Performance implications

No collector, scrape target, scrape interval, or retained series is added.
Prometheus already evaluates these Node Exporter series for the platform-health
dashboard. The only incremental cost is browser-side rendering and ordinary
instant/range queries while the BTC command center is open.
