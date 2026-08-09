from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from neo_tracker.core import Optimizer


Objective = Callable[[dict[str, float]], float]


@dataclass(frozen=True)
class OptimizationResult:
    params: dict[str, float]
    score: float
    history: list[tuple[dict[str, float], float]]


@dataclass(frozen=True)
class GridSearchOptimizer(Optimizer):
    samples_per_axis: int = 5
    maximize: bool = True
    name: str = "grid_search"

    def optimize(self, objective: Objective, search_space: dict[str, tuple[float, float]]) -> OptimizationResult:
        keys = list(search_space)
        axes = [np.linspace(low, high, self.samples_per_axis) for low, high in search_space.values()]
        best_params: dict[str, float] | None = None
        best_score = -np.inf if self.maximize else np.inf
        history: list[tuple[dict[str, float], float]] = []
        for values in np.array(np.meshgrid(*axes)).T.reshape(-1, len(keys)):
            params = {key: float(value) for key, value in zip(keys, values)}
            score = float(objective(params))
            history.append((params, score))
            better = score > best_score if self.maximize else score < best_score
            if better:
                best_score = score
                best_params = params
        return OptimizationResult(params=best_params or {}, score=float(best_score), history=history)

    def to_config(self) -> dict[str, Any]:
        return {"type": self.name, "samples_per_axis": self.samples_per_axis, "maximize": self.maximize}


@dataclass
class ParticleSwarmOptimizer(Optimizer):
    particles: int = 24
    iterations: int = 40
    inertia: float = 0.65
    cognitive: float = 1.4
    social: float = 1.4
    seed: int | None = 7
    maximize: bool = True
    name: str = "particle_swarm"

    def optimize(self, objective: Objective, search_space: dict[str, tuple[float, float]]) -> OptimizationResult:
        rng = np.random.default_rng(self.seed)
        keys = list(search_space)
        lows = np.asarray([search_space[key][0] for key in keys], dtype=float)
        highs = np.asarray([search_space[key][1] for key in keys], dtype=float)
        positions = rng.uniform(lows, highs, size=(self.particles, len(keys)))
        velocities = np.zeros_like(positions)
        personal_best = positions.copy()
        personal_scores = np.asarray([self._score(objective, keys, row) for row in positions])
        best_index = int(np.argmax(personal_scores) if self.maximize else np.argmin(personal_scores))
        global_best = personal_best[best_index].copy()
        global_score = float(personal_scores[best_index])
        history: list[tuple[dict[str, float], float]] = []
        for _ in range(self.iterations):
            r1 = rng.random(size=positions.shape)
            r2 = rng.random(size=positions.shape)
            velocities = (
                self.inertia * velocities
                + self.cognitive * r1 * (personal_best - positions)
                + self.social * r2 * (global_best - positions)
            )
            positions = np.clip(positions + velocities, lows, highs)
            for index, row in enumerate(positions):
                score = self._score(objective, keys, row)
                params = {key: float(value) for key, value in zip(keys, row)}
                history.append((params, score))
                better_personal = score > personal_scores[index] if self.maximize else score < personal_scores[index]
                if better_personal:
                    personal_scores[index] = score
                    personal_best[index] = row.copy()
                better_global = score > global_score if self.maximize else score < global_score
                if better_global:
                    global_score = score
                    global_best = row.copy()
        return OptimizationResult(
            params={key: float(value) for key, value in zip(keys, global_best)},
            score=float(global_score),
            history=history,
        )

    def to_config(self) -> dict[str, Any]:
        return {
            "type": self.name,
            "particles": self.particles,
            "iterations": self.iterations,
            "inertia": self.inertia,
            "cognitive": self.cognitive,
            "social": self.social,
            "seed": self.seed,
            "maximize": self.maximize,
        }

    @staticmethod
    def _score(objective: Objective, keys: list[str], row: np.ndarray) -> float:
        return float(objective({key: float(value) for key, value in zip(keys, row)}))

