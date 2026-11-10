# SILO Score — PUMF Single-Row Re-Identification Risk

> **SILO Score** — *Statistical Isolation & Linkage Odds* — a 0–100
> single-row re-identification risk metric built on the Statistics Canada
> **2021 Census Hierarchical Public Use Microdata File (PUMF, 98M0001X)**.

Given a combination of quasi-identifiers (age, gender, religion, occupation,
province, …), the system looks up how many people in Canada share that
combination (the **group size `X`**, summed from the PUMF `WEIGHT` column) and
converts it into a risk score. The smaller / more *isolated* the group, the
easier a single row is to re-identify — and the higher the score.

It runs two ways:

* **API** — a running HTTP server (`POST /query`, `GET /documentation`, …).
* **Local** — a Python CLI: `python -m idscore.cli query '{...}'`.

Both share the same engine, field dictionary and scoring formula.

---

## 1. The SILO Score formula

**Input:** `X` = population group size (sum of `WEIGHT` for rows matching the
quasi-identifiers; or an unweighted row count if requested).

**Parameters (defaults):** `k_safe = 20`, `X_cap = 1000`, `threshold = 50`.
All three are caller-overridable per request.

> `X = 0` is treated as `X = 1` (an empty cell is as exposed as a unique one).

| Condition | Score |
|---|---|
| `X ≤ 1` | `100` (unique / directly identifiable) |
| `1 < X < k_safe` | `100 − 50 · ln(X) / ln(k_safe)` (unsafe, below k-anonymity) |
| `X = k_safe` | `50` (exactly the passing line) |
| `k_safe ≤ X < X_cap` | `50 · (1 − ln(X / k_safe) / ln(X_cap / k_safe))` (safe) |
| `X ≥ X_cap` | `0` (zero-risk ceiling) |

**Verdict:** `score > threshold → FAIL`, `score ≤ threshold → PASS`.

The two interior branches are continuous at `X = k_safe` (both give 50) and the
safe branch reaches 0 at `X = X_cap`. With the defaults, a group of 20 scores
exactly 50 (the k-anonymity passing line) and a group of 1000+ scores 0.

### Other score types

The `settings.type` field selects the output:

* `scale` *(default)* — the full SILO Score above, with PASS/FAIL and (on FAIL)
  a recommendation naming the most identifiable field.
* `score` — the simple reciprocal `100 / X` percent.
* `size` — just the group size `X`.

---

## 2. Install & build

Dependencies (already present in this environment): `pandas`, `numpy`,
`flask`, `pyarrow`.

```bash
# One-time: parse the StatCan command file into the field dictionary and
# build the in-memory microdata cache (data/fields.json + data/pumf.parquet).
python -m idscore.build_metadata
```

This reads `dataverse_files/Command Files/PUMF2021_Hierarchical_spss_en.sps`
(so every field name, code and label is **identical** to the official StatCan
codebook) and `dataverse_files/Data/data_donnees_2021_hier_v2.csv`.

---

## 3. Local use (CLI)

```bash
# Score a query (JSON string, @file, or - for stdin)
python -m idscore.cli query '{"AGEGRP": 1, "GENDER": "Man+", "PR": 35}'
echo '{"AGEGRP":[12,13],"RELIG":"Sikh"}' | python -m idscore.cli query -

# Documentation, most→least identifying (top N, default all; json or markdown)
python -m idscore.cli docs --n 10
python -m idscore.cli docs --n 10 --format markdown

# Explore fields
python -m idscore.cli fields          # ranked list of all 90 quasi-identifiers
python -m idscore.cli field AGEGRP    # one field's full code table
```

---

## 4. API use (server)

```bash
python -m idscore.server            # http://127.0.0.1:8000  (webpage at /)
PORT=5000 python -m idscore.server  # choose a port
```

| Method & path | Description |
|---|---|
| `POST /query` (alias `POST /score`) | Score a query. Body = the query JSON. |
| `GET /documentation` | Field docs, most→least identifying. Params: `n` (top N, default all), `format` = `json`\|`markdown`. |
| `GET /fields` | Compact ranked field list with code tables. |
| `GET /provinces` | `PR` province code table (for `settings.provinces`). |
| `GET /health` | Liveness probe. |
| `GET /` | Interactive **sample request webpage**. |

```bash
curl -X POST http://127.0.0.1:8000/query -H 'Content-Type: application/json' \
  -d '{"AGEGRP":13,"GENDER":"Woman+","HDGREE":8,"settings":{"provinces":[10,11]}}'

curl "http://127.0.0.1:8000/documentation?n=5&format=markdown"
```

---

## 5. Request format

A request is a JSON object whose keys are **PUMF field names** (see
[`docs/FIELDS.md`](docs/FIELDS.md) or `GET /documentation`). Each field value
may be:

* a single **code** or **label** — `{"AGEGRP": 1}` or `{"GENDER": "Man+"}`
* a **list** of codes/labels — `{"AGEGRP": [1, 2, 3]}`
* `null` or **omitted** — the field is unconstrained

Values accept the **integer code** (canonical, matches the StatCan codebook) or
the **label** (exact case-insensitive, or a unique substring — e.g. `"man"` →
`Man+`).

### `settings` sub-object

```jsonc
"settings": {
  "type":       "scale",      // "scale" (default) | "score" | "size"
  "provinces":  [35, 24],     // PR codes; omit/null = all of Canada
  "population": "weighted",   // "weighted" (default, sums WEIGHT) | "unweighted"
  "X_cap":      1000,         // zero-risk ceiling
  "k_safe":     20,           // k-anonymity passing size (score = 50)
  "threshold":  50            // PASS if score ≤ threshold
}
```

### Example response (`scale`, FAIL)

```jsonc
{
  "metric": "SILO Score",
  "type": "scale",
  "group_size_X": 1.0,
  "result": {
    "silo_score": 100.0, "verdict": "FAIL", "passed": false,
    "band": "unique", "group_size_X": 1.0,
    "parameters": {"k_safe": 20, "X_cap": 1000, "threshold": 50}
  },
  "recommendation": {
    "message": "most identifiable field is NOC21",
    "most_identifiable_field": "NOC21",
    "field_label": "Labour: Broad Occupational Category ...",
    "advice": "Generalize or drop 'NOC21' ...: adding it shrinks the group from 1333345 to 0 (100.0% drop).",
    "reduction_path": [ /* per-field group-size steps */ ]
  }
}
```

On **PASS** in `scale` mode the score is returned without a recommendation.
On **FAIL** the recommendation names the **most identifiable field** (see §7).

---

## 6. Validation & errors

Every field and setting is type- and range-checked. Invalid requests return
`400` with a structured list of problems — unknown fields (with *did-you-mean*
suggestions), out-of-range codes (with the valid code table), bad province
codes, invalid `type`/`population`, and illegal parameters
(`k_safe > 1`, `X_cap > k_safe`, `0 ≤ threshold ≤ 100`).

```jsonc
{ "error": "validation_failed",
  "details": [
    {"field": "AGEGRP", "error": "99 is not a valid code for 'AGEGRP'", "valid_codes": {…}},
    {"field": "GENDR",  "error": "unknown field", "did_you_mean": ["GENDER"]}
  ] }
```

---

## 7. Most identifiable field (FAIL recommendation)

When a `scale` query FAILs, the engine explains *why* via a **greedy reduction
path**. Starting from the province-filtered population, it adds the queried
quasi-identifiers one at a time — always adding next the field that keeps the
group **largest** (the most natural generalization order). Each identifier
therefore produces a visible **group-size step**; the field with the largest
proportional drop is the one making the row most specific, and is reported as
the `most_identifiable_field`. This is robust even when the full combination is
empty (`X = 0`), where a simple leave-one-out cannot localize the culprit.

---

## 8. Field importance & documentation ordering

All documentation (the `/documentation` endpoint, [`docs/FIELDS.md`](docs/FIELDS.md)
and the webpage) is ordered **most → least identifying**. Importance is the
**weighted Shannon entropy** of each field's distribution in the PUMF (ties
broken by category count) — a field that splits the population into many,
evenly-populated categories carries more identifying information than a lopsided
binary flag. `GET /documentation?n=K` returns the top `K` fields.

The system exposes the **90 coded categorical fields** as quasi-identifiers.
Record identifiers (`HH_ID`, `EF_ID`, `CF_ID`, `PP_ID`), the survey weights
(`WEIGHT`, `WT1`–`WT16`) and the 11 continuous dollar-value fields (income,
shelter cost, dwelling value, …) are **excluded** from querying.

---

## 9. The interactive webpage

`GET /` serves a self-contained page to build and run requests:

* every quasi-identifier shown as a **card of checkboxes**, **10 per page**,
  ordered most → least identifying, with page navigation;
* a **mini search bar** inside every large field (e.g. occupation, field of
  study) to filter its options;
* a top-level **filter** to jump to a field by name/label;
* a **Settings panel + Run button at the bottom of every page** (type,
  population, `k_safe`, `X_cap`, `threshold`, and province filter);
* a results panel showing the score, PASS/FAIL, group size and — on FAIL — the
  most-identifiable-field recommendation with the full reduction-path table.

Selections persist across pages, so you can tweak fields anywhere and run.

---

## 10. Project layout

```
idscore/
  build_metadata.py   # parse the SPSS command file -> data/fields.json (+ parquet cache)
  metadata.py         # load field dictionary; resolve codes/labels
  scoring.py          # the SILO Score formula + PASS/FAIL
  query.py            # request parsing, type-checking, structured validation errors
  engine.py           # group-size computation + reduction-path analysis
  docs.py             # documentation generation (ordered most->least identifying)
  cli.py              # local CLI  (python -m idscore.cli ...)
  server.py           # Flask API  (python -m idscore.server)
data/
  fields.json         # generated field dictionary (codes, labels, importance)
  pumf.parquet        # compact in-memory microdata cache
docs/
  FIELDS.md           # full generated codebook (field list + code tables)
web/
  index.html          # interactive sample request webpage
```

---

*Built on StatCan 98M0001X (2021 Census Hierarchical PUMF). The SILO Score is a
disclosure-risk heuristic for PUMF microdata and is not an official Statistics
Canada product.*
