# ADR 0013: Read-Only Storage Expansion Preflight

Status: accepted
Date: 2026-08-21

## Context

The continuous research-data monitor derives a 70.045-day evidence horizon and
projects the bytes needed to retain it. On the deployment host, the 30 GiB root
EBS volume cannot preserve that projected data, the recorder's 5 GiB reserve,
and enough separate space for an uncached image build. A blind resize target
can still leave the partition or ext4 filesystem unchanged, while deleting
capture history or reducing the recorder reserve would invalidate safety and
research assumptions.

The repository has no AWS identity and must not infer permission to alter cloud
resources. Storage expansion therefore needs a deterministic handoff between
live, typed evidence and an explicitly authorized infrastructure operator.

## Decision

- Add `aqt-native storage-expansion-preflight`, a read-only host inspector. It
  consumes only a fresh, content-addressed research-readiness state, a checked
  storage policy, the mounted data path, and Linux sysfs/filesystem metadata.
- Preserve the readiness monitor's projected remaining capture bytes and 5 GiB
  reserve exactly. Add a separate 4 GiB maintenance/build headroom requirement;
  neither amount can be reduced by the command.
- Round the minimum target upward to a 10 GiB allocation boundary. The target
  counts every byte that is currently unavailable to ordinary writers, so
  existing data, filesystem reservation, retention capacity, and maintenance
  capacity remain represented.
- Detect the next incomplete layer: EBS/block device, partition, filesystem, or
  closeout. An unresolvable sysfs layout is an explicit unsupported result,
  never a guessed device command.
- Read sysfs `size` fields in their specified 512-byte sector units while
  retaining the device logical-block size as evidence. Do not multiply sector
  counts by a potentially different logical-block size.
- Write one immutable, content-addressed report per observation. Exit `0` only
  after retention and maintenance headroom pass, `3` for a valid report that
  still requires operator action, and `2` for stale, malformed, missing, or
  conflicting input.
- Keep cloud modification, `growpart`, and `resize2fs` outside application
  code. The runbook requires the operator to re-run the preflight after every
  layer and proceed only when the reported stage matches the intended action.
- Do not restart trading/data services, delete data, change disk floors, clear
  the operator kill, or deploy a new image as part of preflight.

## Alternatives considered

- **Delete old capture or Docker data.** This can destroy immutable research
  lineage or the last deployable image and does not solve the 70-day capacity
  requirement.
- **Lower the 5 GiB recorder floor.** This hides the capacity failure and makes
  an eventual write outage more likely.
- **Hard-code a 50 GiB target.** It fits the current observation but becomes
  stale as capture rate and retained bytes change. The report derives and
  binds the target to the latest readiness evidence.
- **Automatically call AWS and filesystem tools.** This would require cloud
  credentials and destructive host authority in the trading repository, make
  the wrong-device blast radius unacceptable, and bypass the human checkpoint.
- **Use `df` alone.** It cannot distinguish an expanded EBS device from an
  unexpanded partition or filesystem, so it cannot safely identify the next
  layer.
- **Calculate sizes with floating point.** Large byte counts can round
  imprecisely. Integer ceiling division preserves exact allocation boundaries.

## Consequences

- The current host recommendation is derived as 50 GiB: approximately 45.78
  GiB minimum rounded to the checked 10 GiB boundary. It will change if fresh
  capture evidence changes materially before the operator acts.
- Each run performs one readiness JSON read, one `statvfs`-class disk query,
  and a bounded set of tiny sysfs reads. It does not scan raw data, Parquet,
  DuckDB, Docker layers, the network, or the exchange and is not on the trading
  hot path.
- A structurally valid action-required report is not failure noise; it is a
  staged operator instruction. Every subsequent layer requires new evidence
  at a new output path because reports are immutable.
- Device model and serial are evidence for matching the AWS attachment, but the
  operator remains responsible for confirming the instance, volume, region,
  filesystem type, and snapshot before mutation.
- Reaching storage closeout removes only the capacity blocker. It does not
  satisfy capture duration, research, paper, shadow, risk, or promotion gates.
