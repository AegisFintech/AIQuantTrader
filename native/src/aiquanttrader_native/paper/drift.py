"""Bounded online drift monitor using a run-frozen initial feature baseline."""

from __future__ import annotations

from collections import deque

import numpy as np
from numpy.typing import NDArray

from aiquanttrader_native.features.models import MODEL_FEATURE_SCHEMA, MicrostructureSnapshot
from aiquanttrader_native.paper.models import PaperEvidencePolicy
from aiquanttrader_native.research.drift import calculate_drift
from aiquanttrader_native.research.models import DriftReport


class PaperDriftMonitor:
    def __init__(
        self,
        policy: PaperEvidencePolicy,
        *,
        restored_vectors: tuple[tuple[float, ...], ...] = (),
    ) -> None:
        self.policy = policy
        self._baseline: list[NDArray[np.float64]] = []
        self._current: deque[NDArray[np.float64]] = deque(maxlen=policy.drift_window_samples)
        self._observations_after_baseline = 0
        for values in restored_vectors:
            self._append(np.asarray(values, dtype=np.float64))

    @property
    def ready(self) -> bool:
        return len(self._baseline) == self.policy.drift_baseline_samples and (
            len(self._current) == self.policy.drift_window_samples
        )

    def update(self, snapshot: MicrostructureSnapshot) -> DriftReport | None:
        if not snapshot.ready:
            return None
        vector = snapshot.model_vector()
        self._append(vector)
        if not self.ready:
            return None
        if self._observations_after_baseline % self.policy.drift_evaluation_interval_samples != 0:
            return None
        baseline = np.vstack(self._baseline)
        current = np.vstack(self._current)
        return calculate_drift(
            baseline,
            current,
            feature_schema=MODEL_FEATURE_SCHEMA,
            psi_threshold=float(self.policy.maximum_feature_psi),
            mean_shift_threshold=float(self.policy.maximum_standardized_mean_shift),
        )

    def _append(self, vector: NDArray[np.float64]) -> None:
        if vector.shape != (len(MODEL_FEATURE_SCHEMA.features),):
            raise ValueError("paper drift vector does not match the production feature schema")
        if len(self._baseline) < self.policy.drift_baseline_samples:
            self._baseline.append(vector)
            return
        self._current.append(vector)
        self._observations_after_baseline += 1
