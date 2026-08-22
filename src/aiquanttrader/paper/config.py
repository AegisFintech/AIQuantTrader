"""Strict loading and hashing of the complete paper-trading artifact set."""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiquanttrader.backtest.models import ExecutionScenario
from aiquanttrader.backtest.scenarios import load_scenario
from aiquanttrader.config.loader import ConfigBundle
from aiquanttrader.features.models import FeatureEngineConfig
from aiquanttrader.paper.models import PaperEvidencePolicy
from aiquanttrader.paper.simulator import validate_paper_scenario
from aiquanttrader.strategies.market_maker import AvellanedaStoikovConfig
from aiquanttrader.strategies.reactive_scalper import ReactiveScalperConfig
from aiquanttrader.strategies.scalper import OrderFlowScalperConfig

StrategyConfig = AvellanedaStoikovConfig | OrderFlowScalperConfig | ReactiveScalperConfig


@dataclass(frozen=True, slots=True)
class PaperArtifacts:
    scenario: ExecutionScenario
    sensitivity_scenarios: tuple[ExecutionScenario, ...]
    feature_config: FeatureEngineConfig
    strategy_config: StrategyConfig
    evidence_policy: PaperEvidencePolicy
    feature_config_sha256: str
    strategy_config_sha256: str
    evidence_policy_sha256: str


def load_paper_artifacts(config_dir: Path, bundle: ConfigBundle) -> PaperArtifacts:
    root = config_dir.resolve(strict=True)
    paper = bundle.settings.paper
    scenario_path = _resolve(root, paper.scenario_path)
    sensitivity_paths = tuple(_resolve(root, path) for path in paper.sensitivity_scenario_paths)
    feature_path = _resolve(root, paper.feature_config_path)
    strategy_path = _resolve(root, paper.strategy_config_path)
    evidence_path = _resolve(root, paper.evidence_policy_path)

    feature_payload = _read_toml(feature_path)
    strategy_payload = _read_toml(strategy_path)
    evidence_payload = _read_toml(evidence_path)
    feature = FeatureEngineConfig.model_validate(feature_payload)
    if paper.strategy_id == "avellaneda-stoikov-v1":
        strategy: StrategyConfig = AvellanedaStoikovConfig.model_validate(strategy_payload)
    elif paper.strategy_id == "smart-money-scalper-v3":
        strategy = ReactiveScalperConfig.model_validate(strategy_payload)
    else:
        strategy = OrderFlowScalperConfig.model_validate(strategy_payload)
    if strategy.strategy_id != paper.strategy_id:
        raise ValueError("paper strategy configuration does not match selected strategy")

    primary_scenario = load_scenario(scenario_path)
    scenarios = tuple(load_scenario(path) for path in sensitivity_paths)
    validate_paper_scenario(primary_scenario)
    for sensitivity_scenario in scenarios:
        validate_paper_scenario(sensitivity_scenario)
    scenario_ids = {scenario.scenario_id for scenario in scenarios}
    if len(scenario_ids) != len(scenarios):
        raise ValueError("paper sensitivity scenario identities must be unique")
    evidence = PaperEvidencePolicy.model_validate(evidence_payload)
    required_ids = set(evidence.required_sensitivity_scenarios)
    if required_ids != scenario_ids:
        raise ValueError(
            "paper evidence policy and configured sensitivity scenarios must match exactly"
        )
    return PaperArtifacts(
        scenario=primary_scenario,
        sensitivity_scenarios=scenarios,
        feature_config=feature,
        strategy_config=strategy,
        evidence_policy=evidence,
        feature_config_sha256=_sha256(feature_path),
        strategy_config_sha256=_sha256(strategy_path),
        evidence_policy_sha256=_sha256(evidence_path),
    )


def _resolve(root: Path, relative: Path) -> Path:
    candidate = (root / relative).resolve(strict=True)
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise ValueError(f"paper artifact escapes configuration root: {relative}")
    return candidate


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"paper artifact must contain a TOML table: {path}")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
