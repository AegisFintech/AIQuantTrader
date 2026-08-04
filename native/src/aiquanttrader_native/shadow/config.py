"""Strict loading and lineage binding for the complete shadow artifact set."""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiquanttrader_native.backtest.scenarios import load_scenario
from aiquanttrader_native.config.loader import ConfigBundle
from aiquanttrader_native.domain.base import canonical_sha256
from aiquanttrader_native.features.models import FeatureEngineConfig
from aiquanttrader_native.paper.config import PaperArtifacts, StrategyConfig
from aiquanttrader_native.paper.models import PaperEvidencePolicy
from aiquanttrader_native.paper.simulator import validate_paper_scenario
from aiquanttrader_native.shadow.models import ShadowEvidencePolicy
from aiquanttrader_native.strategies.market_maker import AvellanedaStoikovConfig
from aiquanttrader_native.strategies.scalper import OrderFlowScalperConfig


@dataclass(frozen=True, slots=True)
class ShadowArtifacts:
    paper: PaperArtifacts
    evidence_policy: ShadowEvidencePolicy
    evidence_policy_sha256: str
    engine_policy_sha256: str


def load_shadow_artifacts(config_dir: Path, bundle: ConfigBundle) -> ShadowArtifacts:
    root = config_dir.resolve(strict=True)
    selection = bundle.settings.shadow
    scenario_path = _resolve(root, selection.scenario_path)
    sensitivity_paths = tuple(_resolve(root, path) for path in selection.sensitivity_scenario_paths)
    feature_path = _resolve(root, selection.feature_config_path)
    strategy_path = _resolve(root, selection.strategy_config_path)
    engine_policy_path = _resolve(root, selection.engine_policy_path)
    evidence_path = _resolve(root, selection.evidence_policy_path)

    feature = FeatureEngineConfig.model_validate(_read_toml(feature_path))
    strategy_payload = _read_toml(strategy_path)
    strategy: StrategyConfig
    if selection.strategy_id == "avellaneda-stoikov-v1":
        strategy = AvellanedaStoikovConfig.model_validate(strategy_payload)
    else:
        strategy = OrderFlowScalperConfig.model_validate(strategy_payload)
    if strategy.strategy_id != selection.strategy_id:
        raise ValueError("shadow strategy configuration does not match selected strategy")

    primary = load_scenario(scenario_path)
    sensitivity = tuple(load_scenario(path) for path in sensitivity_paths)
    validate_paper_scenario(primary)
    for scenario in sensitivity:
        validate_paper_scenario(scenario)
    if len({scenario.scenario_id for scenario in sensitivity}) != len(sensitivity):
        raise ValueError("shadow sensitivity scenario identities must be unique")

    engine_policy = PaperEvidencePolicy.model_validate(_read_toml(engine_policy_path))
    evidence_policy = ShadowEvidencePolicy.model_validate(_read_toml(evidence_path))
    scenario_ids = {scenario.scenario_id for scenario in sensitivity}
    if scenario_ids != set(evidence_policy.required_sensitivity_scenarios):
        raise ValueError("shadow policy and sensitivity scenarios must match exactly")
    engine_sha = _sha256(engine_policy_path)
    evidence_sha = _sha256(evidence_path)
    combined_policy_sha = canonical_sha256(
        {"engine_policy_sha256": engine_sha, "shadow_policy_sha256": evidence_sha}
    )
    paper = PaperArtifacts(
        scenario=primary,
        sensitivity_scenarios=sensitivity,
        feature_config=feature,
        strategy_config=strategy,
        evidence_policy=engine_policy,
        feature_config_sha256=_sha256(feature_path),
        strategy_config_sha256=_sha256(strategy_path),
        evidence_policy_sha256=combined_policy_sha,
    )
    return ShadowArtifacts(
        paper=paper,
        evidence_policy=evidence_policy,
        evidence_policy_sha256=evidence_sha,
        engine_policy_sha256=engine_sha,
    )


def _resolve(root: Path, relative: Path) -> Path:
    candidate = (root / relative).resolve(strict=True)
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise ValueError(f"shadow artifact escapes configuration root: {relative}")
    return candidate


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"shadow artifact must contain a TOML table: {path}")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
