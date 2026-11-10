"""The SILO Score: Statistical Isolation & Linkage Odds re-identification score.

A single-row re-identification risk metric on a 0-100 scale derived from the
population group size ``X`` (the number of people in Canada who share the
queried combination of quasi-identifiers, from the StatCan 2021 Census PUMF).

Intuition: the more *isolated* an individual is inside their quasi-identifier
group, the easier they are to single out.  A unique person (group of 1) is
maximally exposed (100); a person hidden in a crowd of ``X_cap`` or more is
effectively unexposed (0).

Score anchors (with defaults ``k_safe = 20``, ``X_cap = 1000``):

* ``X <= 1``                 -> 100  (unique / directly identifiable)
* ``1 < X < k_safe``         -> 100 .. 50  (unsafe, below k-anonymity threshold)
* ``X == k_safe``            -> 50   (exactly at the passing line)
* ``k_safe <= X < X_cap``    -> 50 .. 0   (safe, passes k = k_safe)
* ``X >= X_cap``             -> 0    (zero-risk ceiling)

A query PASSES when ``score <= threshold`` (default threshold = 50) and FAILS
when ``score > threshold``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_K_SAFE = 20
DEFAULT_X_CAP = 1000
DEFAULT_THRESHOLD = 50.0


@dataclass(frozen=True)
class ScoreResult:
    score: float          # SILO score, 0..100
    passed: bool          # score <= threshold
    band: str             # qualitative band
    X: float              # group size used (after X=0 -> X=1 rule)
    k_safe: float
    x_cap: float
    threshold: float

    def as_dict(self) -> dict:
        return {
            "silo_score": round(self.score, 4),
            "passed": self.passed,
            "verdict": "PASS" if self.passed else "FAIL",
            "band": self.band,
            "group_size_X": self.X,
            "parameters": {
                "k_safe": self.k_safe,
                "X_cap": self.x_cap,
                "threshold": self.threshold,
            },
        }


def _band(score: float) -> str:
    if score >= 100:
        return "unique"
    if score > 50:
        return "high risk"
    if score == 50:
        return "threshold"
    if score > 0:
        return "low risk"
    return "negligible"


def silo_score(
    X: float,
    k_safe: float = DEFAULT_K_SAFE,
    x_cap: float = DEFAULT_X_CAP,
) -> float:
    """Return the raw SILO score (0..100) for a population group size ``X``.

    ``X = 0`` is treated as ``X = 1`` (an empty cell is as exposed as a unique
    cell).  Parameters are validated by :func:`validate_parameters`.
    """
    if X <= 0:
        X = 1.0  # treat X = 0 as X = 1

    if X <= 1:
        return 100.0
    if X < k_safe:
        return 100.0 - 50.0 * (math.log(X) / math.log(k_safe))
    if X < x_cap:
        return 50.0 * (1.0 - (math.log(X / k_safe) / math.log(x_cap / k_safe)))
    return 0.0


def validate_parameters(k_safe: float, x_cap: float, threshold: float) -> list[str]:
    """Return a list of human-readable problems (empty if parameters are valid)."""
    errors: list[str] = []
    if not (isinstance(k_safe, (int, float)) and k_safe > 1):
        errors.append("k_safe must be a number greater than 1.")
    if not (isinstance(x_cap, (int, float)) and x_cap > 1):
        errors.append("X_cap must be a number greater than 1.")
    if isinstance(k_safe, (int, float)) and isinstance(x_cap, (int, float)) and x_cap <= k_safe:
        errors.append("X_cap must be strictly greater than k_safe.")
    if not (isinstance(threshold, (int, float)) and 0 <= threshold <= 100):
        errors.append("threshold must be a number between 0 and 100.")
    return errors


def evaluate(
    X: float,
    k_safe: float = DEFAULT_K_SAFE,
    x_cap: float = DEFAULT_X_CAP,
    threshold: float = DEFAULT_THRESHOLD,
) -> ScoreResult:
    """Compute the SILO score and PASS/FAIL verdict for a group size."""
    used_X = 1.0 if X <= 0 else float(X)
    score = silo_score(used_X, k_safe=k_safe, x_cap=x_cap)
    return ScoreResult(
        score=score,
        passed=score <= threshold,
        band=_band(score),
        X=used_X,
        k_safe=k_safe,
        x_cap=x_cap,
        threshold=threshold,
    )


def reciprocal_score(X: float) -> float:
    """The simple ``score`` mode: 1/X expressed as a percentage (X=0 -> X=1)."""
    used_X = 1.0 if X <= 0 else float(X)
    return 100.0 / used_X
