# Storage Expansion Runbook

This runbook expands the current AWS EBS-backed ext4 root filesystem without
deleting retained market data, lowering disk floors, changing the operator
kill, or enabling exchange execution. The repository command is read-only;
every infrastructure or filesystem mutation remains an explicit operator act.

AWS documents that Elastic Volumes can increase an attached current-generation
volume without detaching or restarting the instance, that a valuable volume
should be snapshotted first, and that the partition and filesystem must then be
extended. Follow the current AWS procedures for
[requesting the modification](https://docs.aws.amazon.com/ebs/latest/userguide/requesting-ebs-volume-modifications.html),
[monitoring its state](https://docs.aws.amazon.com/ebs/latest/userguide/monitoring-volume-modifications.html),
and
[extending Linux partitions/filesystems](https://docs.aws.amazon.com/ebs/latest/userguide/recognize-expanded-volume-linux.html).

## Safety conditions

- Keep the durable operator kill in its current state. Storage maintenance does
  not authorize orders or production promotion.
- Do not stop or recreate `paper-trader` merely to grow an Elastic Volume. It
  owns the active raw capture, and an avoidable stop breaks the latest
  contiguous research chain.
- Do not run `docker system prune`, delete named volumes, remove raw/normalized
  files, lower the 5 GiB reserve, or build a new image while headroom is low.
- Confirm every device name and reported stage on the target host. The commands
  below are specific to the observed `/dev/nvme0n1p1` ext4 root layout.
- Create and retain an EBS snapshot before the cloud modification. EBS volume
  growth cannot be canceled after submission and cannot be reversed by
  shrinking the same volume.

## 1. Capture the current preflight

The Compose project pins the data and state volume names. Confirm their actual
mountpoints rather than assuming them:

```bash
docker volume inspect aiquanttrader-native-data aiquanttrader-native-state \
  --format '{{.Name}} {{.Mountpoint}}'
findmnt -no SOURCE,FSTYPE,SIZE,AVAIL,TARGET /
lsblk -b -o NAME,MAJ:MIN,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL,SERIAL
mkdir -p state/storage-expansion

uv run --frozen aqt-native storage-expansion-preflight \
  --data-root /var/lib/docker/volumes/aiquanttrader-native-data/_data \
  --readiness-state /var/lib/docker/volumes/aiquanttrader-native-state/_data/research/data-readiness.json \
  --policy configs/operations/storage-expansion-v1.toml \
  --output state/storage-expansion/01-before-ebs.json
```

Exit `3` is expected when the command wrote a valid action-required report.
Exit `0` means no expansion layer remains. Exit `2` means the input was stale,
missing, malformed, or the immutable output path already existed; stop instead
of proceeding without a new valid report.

Inspect the report:

```bash
python3 -m json.tool state/storage-expansion/01-before-ebs.json
```

On 2026-08-21 the live host reported:

- data device `/dev/nvme0n1p1`, parent `/dev/nvme0n1`, ext4 root;
- EBS serial `vol049c601733545d442` (AWS volume
  `vol-049c601733545d442`);
- 30 GiB parent device and approximately 6.25 GiB available;
- approximately 13.73 GiB additional capture, a preserved 5 GiB recorder
  reserve, and a separate 4 GiB maintenance reserve;
- `block_device_resize_required` and a 50 GiB recommendation.

Fresh evidence is authoritative. If the next report recommends more than 50
GiB, use its rounded recommendation; never reuse the historical observation.

## 2. Expand the EBS volume

In the AWS EC2 console for the instance's region:

1. Open **Elastic Block Store > Volumes** and select the volume whose ID,
   instance attachment, root-device mapping, and 30 GiB size all match the
   preflight and `lsblk` evidence.
2. Create a snapshot and wait until AWS records it successfully.
3. Choose **Actions > Modify volume** and set the size to the report's
   `recommended_block_device_bytes` converted to GiB. The current report's
   53,687,091,200 bytes equals exactly 50 GiB.
4. Confirm the modification. Do not change volume type, IOPS, or throughput as
   part of this capacity-only procedure.
5. Wait until the modification is `optimizing` or `completed`. AWS states that
   the increased size is usable at `optimizing`; retain the modification record
   with the preflight evidence.

This host currently has no repository-scoped AWS identity, so the console step
is intentional. Do not put AWS access keys into `.env`, chat, command-line
arguments, logs, or repository files.

Verify that Linux sees the new parent size, then write a new immutable report:

```bash
lsblk -b -o NAME,MAJ:MIN,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL,SERIAL

uv run --frozen aqt-native storage-expansion-preflight \
  --data-root /var/lib/docker/volumes/aiquanttrader-native-data/_data \
  --readiness-state /var/lib/docker/volumes/aiquanttrader-native-state/_data/research/data-readiness.json \
  --policy configs/operations/storage-expansion-v1.toml \
  --output state/storage-expansion/02-after-ebs.json
```

Proceed only if the report says `partition_resize_required`. If it still says
`block_device_resize_required`, confirm the AWS modification, target size, and
Linux device visibility. Do not guess or resize another device.

## 3. Extend partition 1

The current Debian host already provides `growpart` from
`cloud-guest-utils`. Reconfirm the exact partition and filesystem first:

```bash
command -v growpart
findmnt -no SOURCE,FSTYPE,SIZE,AVAIL,TARGET /
lsblk -b -o NAME,MAJ:MIN,SIZE,TYPE,FSTYPE,MOUNTPOINTS
sudo growpart /dev/nvme0n1 1
lsblk -b -o NAME,MAJ:MIN,SIZE,TYPE,FSTYPE,MOUNTPOINTS

uv run --frozen aqt-native storage-expansion-preflight \
  --data-root /var/lib/docker/volumes/aiquanttrader-native-data/_data \
  --readiness-state /var/lib/docker/volumes/aiquanttrader-native-state/_data/research/data-readiness.json \
  --policy configs/operations/storage-expansion-v1.toml \
  --output state/storage-expansion/03-after-partition.json
```

Proceed only if the new report says `filesystem_resize_required`. An
`unsupported_device_layout` result requires investigation and must not be
translated into a guessed partition command.

## 4. Extend ext4 and close out

Run `resize2fs` only after `findmnt` still identifies the root filesystem as
ext4 and the preflight identifies the filesystem layer:

```bash
findmnt -no SOURCE,FSTYPE,SIZE,AVAIL,TARGET /
sudo resize2fs /dev/nvme0n1p1
findmnt -no SOURCE,FSTYPE,SIZE,AVAIL,TARGET /
df -B1 --output=source,fstype,size,used,avail,pcent,target /

uv run --frozen aqt-native storage-expansion-preflight \
  --data-root /var/lib/docker/volumes/aiquanttrader-native-data/_data \
  --readiness-state /var/lib/docker/volumes/aiquanttrader-native-state/_data/research/data-readiness.json \
  --policy configs/operations/storage-expansion-v1.toml \
  --output state/storage-expansion/04-after-filesystem.json
```

Closeout requires exit `0`, stage `ready`, zero
`capacity_shortfall_bytes`, and both `research_retention_ready` and
`maintenance_headroom_ready` equal to `true`. Then verify services and the
readiness capacity gate without restarting capture:

```bash
docker compose ps
curl --fail --silent http://127.0.0.1:9114/metrics \
  | grep '^aqt_research_'
```

Storage closeout only means the host can retain the projected horizon and a
maintenance margin. The 70.045-day continuous capture, research controls,
backtest, paper, shadow, and human promotion gates remain mandatory. A new
paper image may be built and deployed only after closeout and the normal
release checklist pass; the operator kill remains unchanged.

## Failure handling

- Preserve all four reports, `lsblk`, `findmnt`, AWS modification, and snapshot
  evidence. Do not overwrite an existing report path.
- If AWS modification fails, do not run `growpart` or `resize2fs`.
- If partition growth fails, do not run `resize2fs`; capture the exact command
  output and current layout.
- If filesystem growth fails, stop further storage mutation. Keep data services
  in their observed state unless disk pressure or filesystem errors require the
  incident procedure.
- Do not attempt to shrink the volume. Recovery from the snapshot requires a
  separately reviewed replacement/restore plan and explicit operator approval.
