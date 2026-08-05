"""Independent raw-segment normalization worker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aiquanttrader.market_data.catalog import ManifestCatalog
from aiquanttrader.market_data.raw import load_segment_manifest
from aiquanttrader.market_data.storage import (
    QuarantinedSegmentError,
    load_normalized_manifest,
    normalize_segment,
    validate_normalized_files,
)


@dataclass(frozen=True, slots=True)
class NormalizationBatch:
    discovered: int
    normalized: int
    already_complete: int
    quarantined: int


class NormalizationWorker:
    def __init__(self, data_root: Path, catalog: ManifestCatalog) -> None:
        self.data_root = data_root.resolve()
        self.catalog = catalog

    def run_once(self) -> NormalizationBatch:
        normalized_count = 0
        complete_count = 0
        quarantined_count = 0
        manifests = sorted((self.data_root / "raw").rglob("*.manifest.json"))
        for manifest_path in manifests:
            raw = load_segment_manifest(manifest_path)
            normalized_path = (
                self.data_root
                / "normalized"
                / "manifests"
                / f"{raw.segment_id}.normalized.manifest.json"
            )
            if normalized_path.exists():
                normalized = load_normalized_manifest(normalized_path)
                if normalized.source_segment_sha256 != raw.compressed_sha256:
                    raise ValueError(
                        f"normalized source digest differs for segment {raw.segment_id}"
                    )
                validate_normalized_files(normalized, self.data_root)
                self.catalog.register_normalized(normalized)
                complete_count += 1
                continue
            segment_path = self.data_root / raw.relative_path
            try:
                result = normalize_segment(
                    segment_path,
                    output_root=self.data_root,
                    quarantine_root=self.data_root / "quarantine" / "raw-corrupt",
                )
            except QuarantinedSegmentError:
                quarantined_count += 1
                continue
            self.catalog.register_normalized(result.manifest)
            normalized_count += 1
        return NormalizationBatch(
            discovered=len(manifests),
            normalized=normalized_count,
            already_complete=complete_count,
            quarantined=quarantined_count,
        )
