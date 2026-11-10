"""Generate the field documentation, ordered most-to-least identifying.

Powers the ``/documentation`` API endpoint and the static docs file.  Each
field entry carries its PUMF name, label, thematic topic, reference guide,
importance rank/metrics and the full table of codes -> descriptions (identical
to the StatCan codebook).
"""
from __future__ import annotations

from .metadata import Metadata, load_metadata


def build_documentation(meta: Metadata, n: int | None = None) -> dict:
    """Return documentation for the top ``n`` most identifying fields.

    ``n`` defaults to all fields.  Ordering is by importance (weighted entropy,
    then cardinality) — the same ordering used by the sample webpage.
    """
    order = meta.importance_order
    if n is not None:
        order = order[:n]

    fields_doc = []
    for f in order:
        fd = meta.field(f)
        fields_doc.append({
            "rank": fd["importance_rank"],
            "name": fd["name"],
            "label": fd["label"],
            "topic": fd["topic"],
            "reference_guide": fd.get("reference_guide"),
            "type": fd["type"],
            "cardinality": fd["cardinality"],
            "entropy_bits": fd["entropy_bits"],
            "importance": fd["importance"],
            "codes": [
                {"code": int(c), "description": lbl}
                for c, lbl in fd["codes"].items()
            ],
        })

    return {
        "metric": meta.metric_name,
        "metric_long_name": meta._data.get("metric_long_name"),
        "dataset": meta._data.get("dataset"),
        "n_records": meta.n_records,
        "total_fields": len(meta.fields),
        "returned_fields": len(fields_doc),
        "ordering": "most-to-least identifying (weighted Shannon entropy)",
        "fields": fields_doc,
    }


def documentation_markdown(meta: Metadata, n: int | None = None) -> str:
    """Render the documentation as Markdown (field list + code tables)."""
    doc = build_documentation(meta, n)
    lines: list[str] = []
    lines.append(f"# {doc['metric']} — PUMF Field Documentation\n")
    lines.append(f"**Dataset:** {doc['dataset']}  ")
    lines.append(f"**Records:** {doc['n_records']:,}  ")
    lines.append(f"**Fields:** {doc['returned_fields']} of {doc['total_fields']} "
                 f"(ordered {doc['ordering']})\n")

    lines.append("## Field index (most → least identifying)\n")
    lines.append("| Rank | Field | Label | Topic | Categories | Entropy (bits) |")
    lines.append("|-----:|-------|-------|-------|-----------:|---------------:|")
    for fd in doc["fields"]:
        lines.append(
            f"| {fd['rank']} | `{fd['name']}` | {fd['label']} | {fd['topic']} "
            f"| {fd['cardinality']} | {fd['entropy_bits']:.3f} |"
        )
    lines.append("")

    lines.append("## Field code tables\n")
    for fd in doc["fields"]:
        lines.append(f"### {fd['rank']}. {fd['name']} — {fd['label']}\n")
        guide = f" · Reference guide: *{fd['reference_guide']}*" if fd["reference_guide"] else ""
        lines.append(f"*Topic: {fd['topic']}{guide} · "
                     f"Categories: {fd['cardinality']} · "
                     f"Entropy: {fd['entropy_bits']:.3f} bits*\n")
        lines.append("| Code | Description |")
        lines.append("|-----:|-------------|")
        for c in fd["codes"]:
            lines.append(f"| {c['code']} | {c['description']} |")
        lines.append("")
    return "\n".join(lines)
