"""Deterministic causal-label construction for supervised BTC research."""

from __future__ import annotations

import os
import secrets
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from numpy.typing import NDArray

from aiquanttrader.backtest.models import ValidationPlan
from aiquanttrader.features.models import (
    MODEL_FEATURE_SCHEMA,
    FeatureDatasetManifest,
    VolatilityRegime,
)
from aiquanttrader.market_data.io import atomic_write_bytes, fsync_directory, sha256_file
from aiquanttrader.research.models import (
    CausalTrainingMatrix,
    ForecastMatrixManifest,
    ForecastTarget,
)

_ARCHIVE_ARRAYS = {
    "features",
    "labels",
    "sample_ts_ns",
    "volatility_regimes",
    "label_end_ts_ns",
    "feature_schema_sha256",
    "source_dataset_sha256",
}


def require_development_matrix_plan(
    manifest: ForecastMatrixManifest, validation_plan: ValidationPlan
) -> None:
    """Reject a matrix that is not the exact sealed partition for a plan."""

    if validation_plan.dataset_sha256 != manifest.source_dataset_sha256:
        raise ValueError("development matrix source does not match validation plan")
    if validation_plan.label_horizon_ns != manifest.horizon_ns:
        raise ValueError("forecast matrix horizon does not match validation plan")
    if manifest.validation_plan_sha256 != validation_plan.sha256():
        raise ValueError("development matrix does not bind this validation plan")
    if manifest.development_cutoff_ts_ns != validation_plan.final_holdout.start_ts_ns:
        raise ValueError("development matrix cutoff does not match final holdout")


def _safe_output(root: Path, relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)
    if (
        relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
        or not relative_path.endswith(".npz")
    ):
        raise ValueError("forecast matrix output must be a safe relative .npz path")
    resolved_root = root.resolve()
    resolved = (resolved_root / Path(*relative.parts)).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("forecast matrix output escapes its artifact root") from exc
    return resolved


def _write_npy(archive: zipfile.ZipFile, name: str, values: NDArray[np.generic]) -> None:
    info = zipfile.ZipInfo(f"{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o600 << 16
    with archive.open(info, "w", force_zip64=True) as entry:
        np.lib.format.write_array(  # type: ignore[no-untyped-call]
            entry, np.ascontiguousarray(values), allow_pickle=False
        )


def _write_deterministic_npz(path: Path, arrays: dict[str, NDArray[np.generic]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.{secrets.token_hex(8)}.partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        with os.fdopen(descriptor, "w+b", buffering=0, closefd=True) as handle:
            with zipfile.ZipFile(
                handle,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as archive:
                for name in sorted(arrays):
                    _write_npy(archive, name, arrays[name])
            handle.flush()
            os.fsync(handle.fileno())
        digest = sha256_file(partial)
        if path.exists():
            if sha256_file(path) != digest:
                raise FileExistsError(f"immutable forecast matrix differs: {path}")
            partial.unlink()
        else:
            partial.rename(path)
            fsync_directory(path.parent)
        return digest
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _column(table: pa.Table, name: str, dtype: np.dtype[Any]) -> NDArray[Any]:
    if name not in table.column_names:
        raise ValueError(f"feature dataset is missing required column: {name}")
    return np.asarray(table[name].combine_chunks().to_numpy(zero_copy_only=False), dtype=dtype)


def build_forecast_matrix(
    *,
    feature_path: Path,
    feature_manifest_path: Path,
    output_root: Path,
    relative_path: str,
    target: ForecastTarget,
    horizon_ns: int,
    sample_interval_ns: int,
    maximum_label_delay_ns: int,
    validation_plan: ValidationPlan,
) -> tuple[Path, ForecastMatrixManifest]:
    """Seal a plan-bound development matrix without final-holdout-derived rows."""

    if target is not ForecastTarget.NEXT_MID_RETURN_BPS:
        raise ValueError("forecast matrix builder currently supports next mid return only")
    if horizon_ns <= 0 or sample_interval_ns <= 0 or maximum_label_delay_ns < 0:
        raise ValueError("forecast horizon/interval must be positive and label delay non-negative")
    feature_manifest = FeatureDatasetManifest.model_validate_json(
        feature_manifest_path.read_bytes()
    )
    if sha256_file(feature_path) != feature_manifest.file_sha256:
        raise ValueError("feature dataset does not match its immutable manifest")
    if feature_manifest.feature_schema_sha256 != MODEL_FEATURE_SCHEMA.sha256():
        raise ValueError("feature dataset schema is not supported by forecast research")
    if validation_plan.dataset_sha256 != feature_manifest.source_dataset_sha256:
        raise ValueError("validation plan does not match the feature source dataset")
    if validation_plan.label_horizon_ns != horizon_ns:
        raise ValueError("validation plan horizon does not match forecast matrix horizon")

    table = pq.read_table(feature_path)
    if table.num_rows != feature_manifest.row_count:
        raise ValueError("feature dataset row count does not match its manifest")
    schema_hashes = _column(table, "feature_schema_sha256", np.dtype("U64"))
    if not np.all(schema_hashes == MODEL_FEATURE_SCHEMA.sha256()):
        raise ValueError("feature dataset contains a mismatched feature schema")
    timestamps = _column(table, "receive_ts_ns", np.dtype(np.int64))
    if np.any(timestamps < 0) or np.any(np.diff(timestamps) < 0):
        raise ValueError("feature dataset timestamps must be non-negative and ordered")
    if (
        int(timestamps[0]) != feature_manifest.first_receive_ts_ns
        or int(timestamps[-1]) != feature_manifest.last_receive_ts_ns
    ):
        raise ValueError("feature dataset time window does not match its manifest")
    development_cutoff = validation_plan.final_holdout.start_ts_ns
    if not int(timestamps[0]) < development_cutoff <= int(timestamps[-1]):
        raise ValueError("validation-plan holdout boundary is outside the feature dataset")
    ready = _column(table, "ready", np.dtype(np.bool_))
    midprices = _column(table, "midprice", np.dtype(np.float64))
    if not np.all(np.isfinite(midprices)) or np.any(midprices <= 0):
        raise ValueError("feature dataset midprices must be finite and positive")
    feature_values = np.column_stack(
        [_column(table, name, np.dtype(np.float64)) for name in MODEL_FEATURE_SCHEMA.names]
    )
    if not np.all(np.isfinite(feature_values)):
        raise ValueError("feature dataset model values must be finite")
    volatility_regimes = _column(table, "volatility_regime", np.dtype("U6"))
    allowed_regimes = {regime.value for regime in VolatilityRegime}
    if not set(volatility_regimes.tolist()) <= allowed_regimes:
        raise ValueError("feature dataset contains an invalid volatility regime")

    ready_indices = np.flatnonzero(ready)
    if len(ready_indices) < 2:
        raise ValueError("feature dataset has fewer than two ready observations")
    ready_timestamps = timestamps[ready_indices]
    ready_midprices = midprices[ready_indices]
    ready_regimes = volatility_regimes[ready_indices]
    if np.any(ready_regimes == VolatilityRegime.WARMUP.value):
        raise ValueError("ready feature rows cannot retain the warmup volatility regime")
    candidate_positions: list[int] = []
    last_sample_ts: int | None = None
    for position, timestamp in enumerate(ready_timestamps):
        observed = int(timestamp)
        if last_sample_ts is None or observed - last_sample_ts >= sample_interval_ns:
            candidate_positions.append(position)
            last_sample_ts = observed

    sample_positions: list[int] = []
    label_positions: list[int] = []
    dropped_gap = 0
    dropped_tail = 0
    excluded_holdout = 0
    for position in candidate_positions:
        if int(ready_timestamps[position]) >= development_cutoff:
            excluded_holdout += 1
            continue
        target_ts = int(ready_timestamps[position]) + horizon_ns
        label_position = int(np.searchsorted(ready_timestamps, target_ts, side="left"))
        if label_position >= len(ready_timestamps):
            dropped_tail += 1
            continue
        if int(ready_timestamps[label_position]) >= development_cutoff:
            excluded_holdout += 1
            continue
        if int(ready_timestamps[label_position]) - target_ts > maximum_label_delay_ns:
            dropped_gap += 1
            continue
        sample_positions.append(position)
        label_positions.append(label_position)
    if len(sample_positions) < 2:
        raise ValueError("forecast labeling produced fewer than two causal samples")

    samples = np.asarray(sample_positions, dtype=np.int64)
    labels_at = np.asarray(label_positions, dtype=np.int64)
    source_sample_indices = ready_indices[samples]
    matrix = CausalTrainingMatrix(
        features=feature_values[source_sample_indices],
        labels=(
            (ready_midprices[labels_at] - ready_midprices[samples])
            / ready_midprices[samples]
            * 10_000.0
        ),
        sample_ts_ns=ready_timestamps[samples],
        label_end_ts_ns=ready_timestamps[labels_at],
        volatility_regimes=ready_regimes[samples],
        feature_schema=MODEL_FEATURE_SCHEMA,
        source_dataset_sha256=feature_manifest.source_dataset_sha256,
    )
    output = _safe_output(output_root, relative_path)
    file_sha256 = _write_deterministic_npz(
        output,
        {
            "feature_schema_sha256": np.asarray(MODEL_FEATURE_SCHEMA.sha256()),
            "features": matrix.features,
            "label_end_ts_ns": matrix.label_end_ts_ns,
            "labels": matrix.labels,
            "sample_ts_ns": matrix.sample_ts_ns,
            "source_dataset_sha256": np.asarray(matrix.source_dataset_sha256),
            "volatility_regimes": matrix.volatility_regimes,
        },
    )
    regime_counts = {
        regime: int(np.sum(matrix.volatility_regimes == regime.value))
        for regime in (VolatilityRegime.LOW, VolatilityRegime.NORMAL, VolatilityRegime.HIGH)
    }
    manifest = ForecastMatrixManifest.create(
        partition_role="development",
        validation_plan_sha256=validation_plan.sha256(),
        development_cutoff_ts_ns=development_cutoff,
        target=target,
        horizon_ns=horizon_ns,
        sample_interval_ns=sample_interval_ns,
        maximum_label_delay_ns=maximum_label_delay_ns,
        source_feature_dataset_sha256=feature_manifest.feature_dataset_id,
        source_dataset_sha256=feature_manifest.source_dataset_sha256,
        feature_schema_sha256=MODEL_FEATURE_SCHEMA.sha256(),
        causal_matrix_sha256=matrix.sha256(),
        file_sha256=file_sha256,
        source_row_count=table.num_rows,
        ready_row_count=len(ready_indices),
        candidate_row_count=len(candidate_positions),
        row_count=len(matrix.labels),
        low_volatility_row_count=regime_counts[VolatilityRegime.LOW],
        normal_volatility_row_count=regime_counts[VolatilityRegime.NORMAL],
        high_volatility_row_count=regime_counts[VolatilityRegime.HIGH],
        dropped_label_gap_count=dropped_gap,
        dropped_tail_count=dropped_tail,
        excluded_holdout_candidate_count=excluded_holdout,
        first_sample_ts_ns=int(matrix.sample_ts_ns[0]),
        last_sample_ts_ns=int(matrix.sample_ts_ns[-1]),
        first_label_end_ts_ns=int(matrix.label_end_ts_ns[0]),
        last_label_end_ts_ns=int(matrix.label_end_ts_ns[-1]),
    )
    manifest_path = output.with_suffix(output.suffix + ".manifest.json")
    content = manifest.canonical_bytes() + b"\n"
    if manifest_path.exists():
        if manifest_path.read_bytes() != content:
            raise FileExistsError(f"immutable forecast matrix manifest differs: {manifest_path}")
    else:
        atomic_write_bytes(manifest_path, content)
    return manifest_path, manifest


def load_forecast_matrix(
    path: Path, manifest_path: Path
) -> tuple[CausalTrainingMatrix, ForecastMatrixManifest]:
    """Load and revalidate an immutable forecast matrix and all retained lineage."""

    manifest = ForecastMatrixManifest.model_validate_json(manifest_path.read_bytes())
    if sha256_file(path) != manifest.file_sha256:
        raise ValueError("forecast matrix file does not match its manifest")
    with np.load(path, allow_pickle=False) as archive:
        if set(archive.files) != _ARCHIVE_ARRAYS:
            raise ValueError("forecast matrix archive has missing or unexpected arrays")
        schema_hash = str(archive["feature_schema_sha256"].item())
        if schema_hash != MODEL_FEATURE_SCHEMA.sha256():
            raise ValueError("forecast matrix feature schema mismatch")
        matrix = CausalTrainingMatrix(
            features=np.asarray(archive["features"], dtype=np.float64),
            labels=np.asarray(archive["labels"], dtype=np.float64),
            sample_ts_ns=np.asarray(archive["sample_ts_ns"], dtype=np.int64),
            label_end_ts_ns=np.asarray(archive["label_end_ts_ns"], dtype=np.int64),
            volatility_regimes=np.asarray(archive["volatility_regimes"], dtype=np.dtype("U6")),
            feature_schema=MODEL_FEATURE_SCHEMA,
            source_dataset_sha256=str(archive["source_dataset_sha256"].item()),
        )
    if manifest.feature_schema_sha256 != matrix.feature_schema.sha256():
        raise ValueError("forecast matrix manifest feature schema mismatch")
    if manifest.source_dataset_sha256 != matrix.source_dataset_sha256:
        raise ValueError("forecast matrix manifest source dataset mismatch")
    if manifest.causal_matrix_sha256 != matrix.sha256():
        raise ValueError("forecast matrix semantic hash does not match its manifest")
    if manifest.row_count != len(matrix.labels):
        raise ValueError("forecast matrix row count does not match its manifest")
    if np.any(matrix.sample_ts_ns >= manifest.development_cutoff_ts_ns) or np.any(
        matrix.label_end_ts_ns >= manifest.development_cutoff_ts_ns
    ):
        raise ValueError("forecast matrix archive reaches the final holdout")
    observed_regime_counts = (
        int(np.sum(matrix.volatility_regimes == VolatilityRegime.LOW.value)),
        int(np.sum(matrix.volatility_regimes == VolatilityRegime.NORMAL.value)),
        int(np.sum(matrix.volatility_regimes == VolatilityRegime.HIGH.value)),
    )
    manifest_regime_counts = (
        manifest.low_volatility_row_count,
        manifest.normal_volatility_row_count,
        manifest.high_volatility_row_count,
    )
    if observed_regime_counts != manifest_regime_counts:
        raise ValueError("forecast matrix volatility-regime counts do not match its manifest")
    observed_label_delays = matrix.label_end_ts_ns - matrix.sample_ts_ns - manifest.horizon_ns
    if np.any(observed_label_delays < 0) or np.any(
        observed_label_delays > manifest.maximum_label_delay_ns
    ):
        raise ValueError("forecast matrix labels violate the manifest horizon/delay policy")
    if np.any(np.diff(matrix.sample_ts_ns) < manifest.sample_interval_ns):
        raise ValueError("forecast matrix samples violate the manifest interval policy")
    observed_window = (
        int(matrix.sample_ts_ns[0]),
        int(matrix.sample_ts_ns[-1]),
        int(matrix.label_end_ts_ns[0]),
        int(matrix.label_end_ts_ns[-1]),
    )
    manifest_window = (
        manifest.first_sample_ts_ns,
        manifest.last_sample_ts_ns,
        manifest.first_label_end_ts_ns,
        manifest.last_label_end_ts_ns,
    )
    if observed_window != manifest_window:
        raise ValueError("forecast matrix time window does not match its manifest")
    return matrix, manifest
