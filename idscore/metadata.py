"""Load and query the field metadata produced by :mod:`idscore.build_metadata`."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIELDS_JSON = ROOT / "data" / "fields.json"

PROVINCE_FIELD = "PR"


class Metadata:
    def __init__(self, data: dict):
        self._data = data
        self.fields: dict[str, dict] = data["fields"]
        self.continuous_fields: dict[str, dict] = data.get("continuous_fields", {})
        self.importance_order: list[str] = data["importance_order"]
        self.weight_field: str = data["weight_field"]
        self.metric_name: str = data.get("metric_name", "SILO Score")
        self.n_records: int = data.get("n_records", 0)
        # Pre-build lowercase label -> code lookups per field for resolution.
        self._label_index: dict[str, dict[str, int]] = {}
        for fname, fdef in self.fields.items():
            idx: dict[str, int] = {}
            for code, label in fdef["codes"].items():
                idx[label.strip().lower()] = int(code)
            self._label_index[fname] = idx

    # -- field access -----------------------------------------------------
    def is_field(self, name: str) -> bool:
        return name in self.fields

    def field(self, name: str) -> dict:
        return self.fields[name]

    def valid_codes(self, name: str) -> list[int]:
        return [int(c) for c in self.fields[name]["codes"]]

    def code_label(self, name: str, code: int) -> str | None:
        return self.fields[name]["codes"].get(str(code))

    # -- province helpers -------------------------------------------------
    def province_codes(self) -> dict[int, str]:
        return {int(c): l for c, l in self.fields[PROVINCE_FIELD]["codes"].items()}

    # -- value resolution -------------------------------------------------
    def resolve_value(self, field: str, value) -> tuple[int | None, str | None]:
        """Resolve a single user-supplied value to a code.

        Accepts an integer code, a numeric string, or a label string (exact
        case-insensitive, then unique substring match).  Returns
        ``(code, None)`` on success or ``(None, error_message)`` on failure.
        """
        codes = self.fields[field]["codes"]
        # Integer / numeric-string -> treat as code.
        if isinstance(value, bool):
            return None, f"boolean values are not valid for field '{field}'"
        if isinstance(value, int):
            return (int(value), None) if str(value) in codes else (
                None,
                f"{value} is not a valid code for '{field}'",
            )
        if isinstance(value, float):
            if value.is_integer() and str(int(value)) in codes:
                return int(value), None
            return None, f"{value} is not a valid code for '{field}'"
        if isinstance(value, str):
            s = value.strip()
            if s.lstrip("-").isdigit():
                return (int(s), None) if s in codes else (
                    None,
                    f"{s} is not a valid code for '{field}'",
                )
            low = s.lower()
            idx = self._label_index[field]
            if low in idx:
                return idx[low], None
            # Unique substring match on labels.
            hits = [code for lbl, code in idx.items() if low in lbl]
            if len(hits) == 1:
                return hits[0], None
            if len(hits) > 1:
                return None, (
                    f"label '{value}' is ambiguous for '{field}' "
                    f"({len(hits)} matches); use an exact code"
                )
            return None, f"'{value}' is not a valid code or label for '{field}'"
        return None, f"unsupported value type {type(value).__name__} for '{field}'"


@lru_cache(maxsize=1)
def load_metadata(path: str | None = None) -> Metadata:
    p = Path(path) if path else FIELDS_JSON
    data = json.loads(p.read_text(encoding="utf-8"))
    return Metadata(data)
