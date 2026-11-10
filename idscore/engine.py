"""Core re-identification engine: group-size computation + SILO scoring.

Loads the compact parquet cache once into memory and answers queries by
summing ``WEIGHT`` (or counting rows) over the subset of the PUMF that matches
the requested quasi-identifier constraints.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from . import scoring
from .metadata import Metadata, PROVINCE_FIELD, load_metadata
from .query import Query, Settings

ROOT = Path(__file__).resolve().parent.parent
PARQUET = ROOT / "data" / "pumf.parquet"


class Engine:
    def __init__(self, df: pd.DataFrame, meta: Metadata):
        self.df = df
        self.meta = meta
        self.weight_col = meta.weight_field

    # -- masking ----------------------------------------------------------
    def _province_mask(self, provinces: list[int] | None) -> np.ndarray:
        if not provinces:
            return np.ones(len(self.df), dtype=bool)
        return self.df[PROVINCE_FIELD].isin(provinces).to_numpy()

    def _constraint_mask(self, constraints: dict[str, list[int]]) -> np.ndarray:
        mask = np.ones(len(self.df), dtype=bool)
        for f, codes in constraints.items():
            mask &= self.df[f].isin(codes).to_numpy()
        return mask

    def group_size(
        self,
        constraints: dict[str, list[int]],
        provinces: list[int] | None,
        weighted: bool,
    ) -> float:
        mask = self._constraint_mask(constraints) & self._province_mask(provinces)
        if weighted:
            return float(self.df[self.weight_col].to_numpy()[mask].sum())
        return float(int(mask.sum()))

    # -- most identifiable field -----------------------------------------
    def leave_one_out(
        self,
        constraints: dict[str, list[int]],
        provinces: list[int] | None,
        weighted: bool,
    ) -> list[dict]:
        """For each constrained field, compute the group size with that field
        relaxed (useful when the group is non-empty)."""
        base = self.group_size(constraints, provinces, weighted)
        rows: list[dict] = []
        for f in constraints:
            reduced = {k: v for k, v in constraints.items() if k != f}
            without = self.group_size(reduced, provinces, weighted)
            rows.append({
                "field": f,
                "label": self.meta.field(f)["label"],
                "group_size_with_field": base,
                "group_size_without_field": without,
                "delta": without - base,
            })
        rows.sort(key=lambda r: r["delta"], reverse=True)
        return rows

    def reduction_path(
        self,
        constraints: dict[str, list[int]],
        provinces: list[int] | None,
        weighted: bool,
    ) -> list[dict]:
        """Greedy forward build of the group.

        Starting from the province-filtered population, we add the queried
        quasi-identifiers one at a time, always adding next the field that keeps
        the group *largest* (the most natural generalization order).  This makes
        each identifier's "group-size step" visible; the field with the largest
        proportional drop is the one making the row most specific.
        """
        applied: dict[str, list[int]] = {}
        remaining = dict(constraints)
        size_prev = self.group_size({}, provinces, weighted)
        path: list[dict] = []
        while remaining:
            best_f, best_size = None, -1.0
            for f, codes in remaining.items():
                trial = dict(applied)
                trial[f] = codes
                sz = self.group_size(trial, provinces, weighted)
                if sz > best_size:
                    best_size, best_f = sz, f
            drop = size_prev - best_size
            drop_ratio = (drop / size_prev) if size_prev > 0 else 0.0
            path.append({
                "field": best_f,
                "label": self.meta.field(best_f)["label"],
                "group_size_before": size_prev,
                "group_size_after": best_size,
                "drop": drop,
                "drop_ratio": round(drop_ratio, 6),
            })
            applied[best_f] = remaining.pop(best_f)
            size_prev = best_size
        return path

    def most_identifiable_field(
        self,
        constraints: dict[str, list[int]],
        provinces: list[int] | None,
        weighted: bool,
    ) -> dict | None:
        """Return the step from the reduction path with the largest proportional
        group-size drop — the field that makes the row most specific."""
        if not constraints:
            return None
        path = self.reduction_path(constraints, provinces, weighted)
        return max(path, key=lambda s: (s["drop_ratio"], s["drop"]))

    # -- top level evaluation --------------------------------------------
    def run(self, query: Query) -> dict:
        s: Settings = query.settings
        X = self.group_size(query.constraints, s.provinces, s.weighted)

        result: dict = {
            "metric": self.meta.metric_name,
            "type": s.type,
            "query": query.describe(self.meta),
            "settings": {
                "type": s.type,
                "population": s.population,
                "provinces": self._province_echo(s.provinces),
                "k_safe": s.k_safe,
                "X_cap": s.x_cap,
                "threshold": s.threshold,
            },
            "group_size_X": X,
        }

        if s.type == "size":
            result["result"] = {"group_size_X": 1.0 if X <= 0 else X}
            return result

        if s.type == "score":
            result["result"] = {
                "reciprocal_score_pct": round(scoring.reciprocal_score(X), 6),
                "group_size_X": 1.0 if X <= 0 else X,
            }
            return result

        # type == "scale": full SILO score + verdict (+ recommendation on FAIL)
        sr = scoring.evaluate(X, k_safe=s.k_safe, x_cap=s.x_cap, threshold=s.threshold)
        result["result"] = sr.as_dict()
        if not sr.passed and query.constraints:
            path = self.reduction_path(query.constraints, s.provinces, s.weighted)
            mif = max(path, key=lambda st: (st["drop_ratio"], st["drop"]))
            result["recommendation"] = {
                "message": f"most identifiable field is {mif['field']}",
                "most_identifiable_field": mif["field"],
                "field_label": mif["label"],
                "advice": (
                    f"Generalize or drop '{mif['field']}' ({mif['label']}): "
                    f"adding it shrinks the group from "
                    f"{mif['group_size_before']:.0f} to {mif['group_size_after']:.0f} "
                    f"({mif['drop_ratio'] * 100:.1f}% drop)."
                ),
                "reduction_path": path,
            }
        return result

    def _province_echo(self, provinces: list[int] | None):
        if not provinces:
            return "all"
        return [{"code": p, "label": self.meta.code_label(PROVINCE_FIELD, p)}
                for p in provinces]


@lru_cache(maxsize=1)
def load_engine() -> Engine:
    meta = load_metadata()
    df = pd.read_parquet(PARQUET)
    return Engine(df, meta)
