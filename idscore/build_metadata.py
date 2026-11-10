"""Build the field metadata dictionary for the PUMF re-identification system.

This parses the official Statistics Canada SPSS command file
(``PUMF2021_Hierarchical_spss_en.sps``) so that every field name, label and
value code used by this system is **identical** to the one published by
Statistics Canada.  It then scans the microdata once to attach a distribution
based *importance* (weighted Shannon entropy + cardinality) to every field.

Output: ``data/fields.json``.

Run with::

    python -m idscore.build_metadata
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SPS_FILE = ROOT / "dataverse_files" / "Command Files" / "PUMF2021_Hierarchical_spss_en.sps"
CSV_FILE = ROOT / "dataverse_files" / "Data" / "data_donnees_2021_hier_v2.csv"
OUT_DIR = ROOT / "data"
FIELDS_JSON = OUT_DIR / "fields.json"
PARQUET = OUT_DIR / "pumf.parquet"

WEIGHT_FIELD = "WEIGHT"

# Record identifiers + replicate weights are never quasi-identifiers.
ID_FIELDS = {"HH_ID", "EF_ID", "CF_ID", "PP_ID"}
WEIGHT_FIELDS = {WEIGHT_FIELD} | {f"WT{i}" for i in range(1, 17)}

# Reference-guide PDF that documents each thematic prefix (for cross-reference).
TOPIC_GUIDE = {
    "Indigenous": "Indigenous Peoples Reference Guide",
    "Income": "Income Reference Guide",
    "Language": "Languages Reference Guide",
    "Labour": "Labour Reference Guide",
    "Education": "Education Reference Guide",
    "Immigration": "Place of Birth, Generation Status, Citizenship and Immigration Reference Guide",
    "Citizenship": "Place of Birth, Generation Status, Citizenship and Immigration Reference Guide",
    "Generation status": "Place of Birth, Generation Status, Citizenship and Immigration Reference Guide",
    "Commuting": "Commuting Reference Guide",
    "Religion": "Religion Reference Guide",
    "Ethnic or cultural origin": "Ethnic or Cultural Origin Reference Guide",
    "Mobility 1": "Mobility and Migration Reference Guide",
    "Mobility 5": "Mobility and Migration Reference Guide",
}


def _read_sps_text() -> str:
    return SPS_FILE.read_text(encoding="utf-8", errors="replace")


def parse_data_list(text: str) -> list[str]:
    """Return variable names in file order from the DATA LIST block."""
    m = re.search(r"DATA LIST\s+FILE=DATA/(.*?)\.\s*\n", text, re.DOTALL)
    block = m.group(1)
    names = re.findall(r"([A-Z0-9_]+)\s+\d+(?:-\d+)?", block)
    return names


def parse_variable_labels(text: str, varnames: list[str]) -> dict[str, str]:
    m = re.search(r"VARIABLE LABELS(.*?)VALUE LABELS", text, re.DOTALL)
    block = m.group(1)
    # Alternation of known names, longest first to avoid prefix collisions.
    alt = "|".join(sorted(varnames, key=len, reverse=True))
    pattern = re.compile(
        rf"\b({alt})\s+((?:'[^']*'\s*\+?\s*)+)", re.DOTALL
    )
    labels: dict[str, str] = {}
    for name, raw in pattern.findall(block):
        parts = re.findall(r"'([^']*)'", raw)
        labels[name] = "".join(parts).strip()
    return labels


def parse_value_labels(text: str) -> dict[str, dict[str, str]]:
    m = re.search(r"VALUE LABELS(.*?)\n\s*\.\s*\nSAVE", text, re.DOTALL)
    block = m.group(1)
    result: dict[str, dict[str, str]] = {}
    for chunk in block.split("\n /"):
        chunk = chunk.strip()
        if not chunk:
            continue
        name_m = re.match(r"([A-Z0-9_]+)", chunk)
        if not name_m:
            continue
        name = name_m.group(1)
        pairs = re.findall(r"(-?\d+)\s+\"([^\"]*)\"", chunk)
        if pairs:
            result[name] = {code: lbl for code, lbl in pairs}
    return result


def parse_positions(text: str) -> dict[str, list[int]]:
    m = re.search(r"DATA LIST\s+FILE=DATA/(.*?)\.\s*\n", text, re.DOTALL)
    block = m.group(1)
    pos: dict[str, list[int]] = {}
    for name, rng in re.findall(r"([A-Z0-9_]+)\s+(\d+(?:-\d+)?)", block):
        if "-" in rng:
            a, b = rng.split("-")
            pos[name] = [int(a), int(b)]
        else:
            pos[name] = [int(rng), int(rng)]
    return pos


def topic_of(label: str) -> str:
    if ":" in label:
        return label.split(":", 1)[0].strip()
    # A few thematic variables use a non-colon phrasing.
    for key in TOPIC_GUIDE:
        if label.startswith(key):
            return key
    return "General"


def compute_importance(queryable: list[str]) -> dict[str, dict]:
    """Scan the microdata once and compute weighted entropy + cardinality."""
    usecols = queryable + [WEIGHT_FIELD]
    df = pd.read_csv(CSV_FILE, usecols=usecols)
    stats: dict[str, dict] = {}
    w = df[WEIGHT_FIELD].astype(float)
    total_w = float(w.sum())
    for f in queryable:
        grp = df.groupby(f, observed=True)[WEIGHT_FIELD].sum()
        p = (grp / total_w).to_numpy()
        p = p[p > 0]
        entropy = float(-sum(pi * math.log2(pi) for pi in p))
        card = int((grp > 0).sum())
        stats[f] = {
            "cardinality": card,
            "entropy_bits": round(entropy, 4),
            "max_entropy_bits": round(math.log2(card), 4) if card > 1 else 0.0,
        }
    return stats


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    text = _read_sps_text()
    varnames = parse_data_list(text)
    labels = parse_variable_labels(text, varnames)
    value_labels = parse_value_labels(text)
    positions = parse_positions(text)

    # Queryable quasi-identifiers = coded categorical fields (have value labels),
    # excluding record ids and weights.
    queryable = [
        v for v in varnames
        if v in value_labels and v not in ID_FIELDS and v not in WEIGHT_FIELDS
    ]
    continuous = [
        v for v in varnames
        if v not in value_labels and v not in ID_FIELDS and v not in WEIGHT_FIELDS
    ]

    print(f"{len(varnames)} variables; {len(queryable)} queryable, "
          f"{len(continuous)} continuous, {len(ID_FIELDS)} ids, "
          f"{len(WEIGHT_FIELDS)} weights")

    print("Scanning microdata for importance ...")
    stats = compute_importance(queryable)

    fields: dict[str, dict] = {}
    for v in queryable:
        label = labels.get(v, v)
        topic = topic_of(label)
        fields[v] = {
            "name": v,
            "label": label,
            "topic": topic,
            "reference_guide": TOPIC_GUIDE.get(topic),
            "position": positions.get(v),
            "type": "categorical",
            "queryable": True,
            "codes": value_labels[v],
            "cardinality": stats[v]["cardinality"],
            "entropy_bits": stats[v]["entropy_bits"],
            "max_entropy_bits": stats[v]["max_entropy_bits"],
            "importance": stats[v]["entropy_bits"],
        }

    continuous_fields: dict[str, dict] = {}
    for v in continuous:
        label = labels.get(v, v)
        continuous_fields[v] = {
            "name": v,
            "label": label,
            "topic": topic_of(label),
            "position": positions.get(v),
            "type": "continuous",
            "queryable": False,
            "note": "Continuous value in the microdata; not exposed as a quasi-identifier.",
        }

    # Importance ordering: most identifying first. Primary = entropy, then cardinality.
    order = sorted(
        fields,
        key=lambda f: (fields[f]["entropy_bits"], fields[f]["cardinality"]),
        reverse=True,
    )
    for rank, f in enumerate(order, start=1):
        fields[f]["importance_rank"] = rank

    meta = {
        "metric_name": "SILO Score",
        "metric_long_name": "Statistical Isolation & Linkage Odds re-identification score",
        "source_file": SPS_FILE.name,
        "dataset": "2021 Census Hierarchical PUMF (StatCan 98M0001X)",
        "n_records": 361915,
        "weight_field": WEIGHT_FIELD,
        "id_fields": sorted(ID_FIELDS),
        "weight_fields": sorted(WEIGHT_FIELDS),
        "importance_order": order,
        "fields": fields,
        "continuous_fields": continuous_fields,
    }
    FIELDS_JSON.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    print(f"Wrote {FIELDS_JSON} ({len(fields)} queryable fields).")

    # Build a compact in-memory parquet cache (queryable coded fields + weight).
    print("Building parquet cache ...")
    usecols = queryable + [WEIGHT_FIELD, "PR"]
    usecols = list(dict.fromkeys(usecols))
    df = pd.read_csv(CSV_FILE, usecols=usecols)
    for f in queryable:
        df[f] = df[f].astype("int32")
    df[WEIGHT_FIELD] = df[WEIGHT_FIELD].astype("float64")
    df.to_parquet(PARQUET, index=False)
    print(f"Wrote {PARQUET} ({len(df)} rows, {len(df.columns)} cols).")


if __name__ == "__main__":
    main()
