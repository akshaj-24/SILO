"""Parse, validate and type-check incoming re-identification queries.

A request is a JSON object whose keys are PUMF field names.  Each field value
may be:

* a single code or label        -> ``{"AGEGRP": 1}`` / ``{"GENDER": "Man+"}``
* a list of codes or labels      -> ``{"AGEGRP": [1, 2, 3]}``
* ``null`` or omitted            -> the field is unconstrained

An optional ``settings`` sub-object controls how the group is scored::

    "settings": {
        "type": "scale" | "score" | "size",   # default "scale"
        "provinces": [35, 24],                  # PR codes, default all
        "population": "weighted" | "unweighted", # default "weighted"
        "X_cap": 1000,
        "k_safe": 20,
        "threshold": 50
    }

:class:`QueryValidationError` carries a structured list of problems so callers
(API or CLI) can return precise, field-level errors.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field

from .metadata import Metadata, PROVINCE_FIELD
from . import scoring

VALID_TYPES = {"scale", "score", "size"}
VALID_POPULATION = {"weighted", "unweighted"}
SETTINGS_KEYS = {"type", "provinces", "population", "X_cap", "k_safe", "threshold"}


class QueryValidationError(Exception):
    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__(f"{len(errors)} validation error(s)")

    def as_dict(self) -> dict:
        return {"error": "validation_failed", "details": self.errors}


@dataclass
class Settings:
    type: str = "scale"
    provinces: list[int] | None = None
    population: str = "weighted"
    x_cap: float = scoring.DEFAULT_X_CAP
    k_safe: float = scoring.DEFAULT_K_SAFE
    threshold: float = scoring.DEFAULT_THRESHOLD

    @property
    def weighted(self) -> bool:
        return self.population == "weighted"


@dataclass
class Query:
    constraints: dict[str, list[int]] = dc_field(default_factory=dict)
    settings: Settings = dc_field(default_factory=Settings)
    raw: dict = dc_field(default_factory=dict)

    def describe(self, meta: Metadata) -> dict:
        """Human-readable echo of the constraints (code -> label)."""
        out = {}
        for f, codes in self.constraints.items():
            out[f] = [
                {"code": c, "label": meta.code_label(f, c)} for c in codes
            ]
        return out


def _err(field, message, **extra) -> dict:
    d = {"field": field, "error": message}
    d.update(extra)
    return d


def _parse_settings(raw: dict, meta: Metadata, errors: list[dict]) -> Settings:
    s = Settings()
    if raw is None:
        return s
    if not isinstance(raw, dict):
        errors.append(_err("settings", "settings must be an object"))
        return s

    unknown = set(raw) - SETTINGS_KEYS
    for k in sorted(unknown):
        errors.append(_err(f"settings.{k}", "unknown setting",
                           allowed=sorted(SETTINGS_KEYS)))

    if "type" in raw:
        t = raw["type"]
        if t in VALID_TYPES:
            s.type = t
        else:
            errors.append(_err("settings.type", "invalid type",
                               allowed=sorted(VALID_TYPES), received=t))

    if "population" in raw:
        p = raw["population"]
        if p in VALID_POPULATION:
            s.population = p
        else:
            errors.append(_err("settings.population", "invalid population",
                               allowed=sorted(VALID_POPULATION), received=p))

    if "provinces" in raw and raw["provinces"] is not None:
        provs = raw["provinces"]
        if not isinstance(provs, list):
            provs = [provs]
        valid_pr = set(meta.valid_codes(PROVINCE_FIELD))
        resolved: list[int] = []
        for p in provs:
            code, perr = meta.resolve_value(PROVINCE_FIELD, p)
            if perr or code not in valid_pr:
                errors.append(_err("settings.provinces",
                                   f"invalid province '{p}'",
                                   valid_codes=meta.province_codes()))
            else:
                resolved.append(code)
        s.provinces = resolved or None

    for key, attr in (("x_cap", "x_cap"), ("X_cap", "x_cap"),
                      ("k_safe", "k_safe"), ("threshold", "threshold")):
        if key in raw and raw[key] is not None:
            v = raw[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                errors.append(_err(f"settings.{key}", "must be a number",
                                   received=v))
            else:
                setattr(s, attr, float(v))

    for problem in scoring.validate_parameters(s.k_safe, s.x_cap, s.threshold):
        errors.append(_err("settings", problem))

    return s


def parse_query(payload: dict, meta: Metadata) -> Query:
    """Validate ``payload`` and return a :class:`Query`.

    Raises :class:`QueryValidationError` with a structured error list on any
    problem.
    """
    errors: list[dict] = []
    if not isinstance(payload, dict):
        raise QueryValidationError([_err(None, "request body must be a JSON object")])

    settings_raw = payload.get("settings")
    settings = _parse_settings(settings_raw, meta, errors)

    constraints: dict[str, list[int]] = {}
    for key, value in payload.items():
        if key == "settings":
            continue
        if value is None:
            continue  # explicitly unconstrained
        if not meta.is_field(key):
            suggestion = _suggest(key, meta)
            errors.append(_err(key, "unknown field", did_you_mean=suggestion))
            continue

        values = value if isinstance(value, list) else [value]
        if isinstance(value, list) and len(value) == 0:
            continue  # empty list -> unconstrained
        codes: list[int] = []
        for v in values:
            code, verr = meta.resolve_value(key, v)
            if verr:
                errors.append(_err(key, verr,
                                   valid_codes=meta.field(key)["codes"]))
            else:
                codes.append(code)
        if codes:
            constraints[key] = sorted(set(codes))

    if errors:
        raise QueryValidationError(errors)

    return Query(constraints=constraints, settings=settings, raw=payload)


def _suggest(key: str, meta: Metadata) -> list[str]:
    import difflib
    ku = key.upper()
    sub = [f for f in meta.fields if ku in f or f in ku]
    close = difflib.get_close_matches(ku, list(meta.fields), n=5, cutoff=0.6)
    out: list[str] = []
    for f in sub + close:
        if f not in out:
            out.append(f)
    return out[:5]
