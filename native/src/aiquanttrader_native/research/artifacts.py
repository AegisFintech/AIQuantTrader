"""Atomic, hash-bound model-native artifact persistence; pickle is forbidden."""

from __future__ import annotations

import os
import secrets
from datetime import datetime
from pathlib import Path, PurePosixPath

from aiquanttrader_native.domain.base import canonical_sha256
from aiquanttrader_native.features.models import FeatureSchema
from aiquanttrader_native.market_data.io import atomic_write_bytes, fsync_directory, sha256_file
from aiquanttrader_native.research.model_adapters import TrainedModel, adapter_for
from aiquanttrader_native.research.models import (
    MODEL_FORMAT_BY_ENGINE,
    MODEL_SUFFIX_BY_FORMAT,
    ForecastTarget,
    ModelArtifactManifest,
)


def _artifact_path(root: Path, relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("artifact path must be safe and relative")
    resolved_root = root.resolve()
    resolved = (resolved_root / Path(*relative.parts)).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("artifact path escapes model root") from exc
    return resolved


def save_model_artifact(
    model: TrainedModel,
    *,
    artifact_root: Path,
    relative_path: str,
    training_dataset_sha256: str,
    training_window_sha256: str,
    dependency_lock_sha256: str,
    created_at: datetime,
) -> tuple[Path, ModelArtifactManifest]:
    model_format = MODEL_FORMAT_BY_ENGINE[model.engine]
    suffix = MODEL_SUFFIX_BY_FORMAT[model_format]
    if not relative_path.endswith(suffix):
        raise ValueError(f"{model.engine.value} artifacts must end with {suffix}")
    final_path = _artifact_path(artifact_root, relative_path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    partial = final_path.with_name(f".{final_path.stem}.{secrets.token_hex(8)}{suffix}")
    adapter = adapter_for(model.engine)
    try:
        adapter.save(model, partial)
        descriptor = os.open(partial, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        digest = sha256_file(partial)
        byte_count = partial.stat().st_size
        if byte_count <= 0:
            raise ValueError("model adapter wrote an empty artifact")
        if final_path.exists():
            if sha256_file(final_path) != digest:
                raise FileExistsError(f"immutable model artifact differs: {final_path}")
            partial.unlink()
        else:
            partial.rename(final_path)
            fsync_directory(final_path.parent)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    parameters_sha256 = canonical_sha256(model.parameters)
    identity = {
        "engine": model.engine,
        "model_format": model_format,
        "target": model.target,
        "relative_path": relative_path,
        "artifact_sha256": digest,
        "artifact_bytes": byte_count,
        "feature_schema_sha256": model.feature_schema.sha256(),
        "training_dataset_sha256": training_dataset_sha256,
        "training_window_sha256": training_window_sha256,
        "parameters_sha256": parameters_sha256,
        "dependency_lock_sha256": dependency_lock_sha256,
    }
    manifest = ModelArtifactManifest(
        model_id=canonical_sha256(identity),
        engine=model.engine,
        model_format=model_format,
        target=model.target,
        relative_path=relative_path,
        artifact_sha256=digest,
        artifact_bytes=byte_count,
        feature_schema_sha256=model.feature_schema.sha256(),
        training_dataset_sha256=training_dataset_sha256,
        training_window_sha256=training_window_sha256,
        parameters_sha256=parameters_sha256,
        dependency_lock_sha256=dependency_lock_sha256,
        created_at=created_at,
    )
    manifest_path = final_path.with_suffix(final_path.suffix + ".manifest.json")
    content = manifest.canonical_bytes() + b"\n"
    if manifest_path.exists():
        if manifest_path.read_bytes() != content:
            raise FileExistsError(f"immutable model manifest differs: {manifest_path}")
    else:
        atomic_write_bytes(manifest_path, content)
    return manifest_path, manifest


def load_model_artifact(
    *,
    artifact_root: Path,
    manifest_path: Path,
    feature_schema: FeatureSchema,
    expected_target: ForecastTarget | None = None,
) -> TrainedModel:
    manifest = ModelArtifactManifest.model_validate_json(manifest_path.read_bytes())
    if manifest.feature_schema_sha256 != feature_schema.sha256():
        raise ValueError("model artifact feature schema mismatch")
    if expected_target is not None and manifest.target is not expected_target:
        raise ValueError("model artifact target mismatch")
    artifact_path = _artifact_path(artifact_root, manifest.relative_path)
    if artifact_path.stat().st_size != manifest.artifact_bytes:
        raise ValueError("model artifact byte count mismatch")
    if sha256_file(artifact_path) != manifest.artifact_sha256:
        raise ValueError("model artifact hash mismatch")
    return adapter_for(manifest.engine).load(
        artifact_path,
        target=manifest.target,
        feature_schema=feature_schema,
    )
