"""Flask API server for the SILO Score re-identification system.

Endpoints
---------
* ``POST /query``          Score a query (body = the query JSON).
* ``POST /score``          Alias of /query.
* ``GET  /documentation``  Field documentation, most→least identifying.
                           Query params: ``n`` (top N, default all),
                           ``format`` = ``json`` (default) | ``markdown``.
* ``GET  /fields``         Compact field list (name, label, rank, codes count).
* ``GET  /provinces``      Province (PR) code table (for the provinces setting).
* ``GET  /health``         Liveness probe.
* ``GET  /``               Serves the interactive sample request webpage.

Run with::

    python -m idscore.server            # http://127.0.0.1:8000
    PORT=5000 python -m idscore.server
"""
from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from .docs import build_documentation, documentation_markdown
from .engine import load_engine
from .metadata import PROVINCE_FIELD, load_metadata
from .query import QueryValidationError, parse_query

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    meta = load_metadata()

    # Warm the engine (loads parquet into memory) at startup.
    engine = load_engine()

    @app.after_request
    def cors(resp):  # allow the local webpage / cross-origin callers
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
        return resp

    @app.route("/query", methods=["POST", "OPTIONS"])
    @app.route("/score", methods=["POST", "OPTIONS"])
    def query_endpoint():
        if request.method == "OPTIONS":
            return ("", 204)
        payload = request.get_json(silent=True)
        if payload is None:
            return jsonify({"error": "invalid_json",
                            "message": "request body must be valid JSON"}), 400
        try:
            q = parse_query(payload, meta)
        except QueryValidationError as e:
            return jsonify(e.as_dict()), 400
        return jsonify(engine.run(q))

    @app.route("/documentation", methods=["GET"])
    def documentation_endpoint():
        n = request.args.get("n", default=None, type=int)
        if n is not None and n < 0:
            return jsonify({"error": "invalid_parameter",
                            "message": "n must be >= 0"}), 400
        fmt = request.args.get("format", default="json")
        if fmt == "markdown":
            return Response(documentation_markdown(meta, n),
                            mimetype="text/markdown")
        return jsonify(build_documentation(meta, n))

    @app.route("/fields", methods=["GET"])
    def fields_endpoint():
        out = []
        for f in meta.importance_order:
            fd = meta.field(f)
            out.append({
                "rank": fd["importance_rank"],
                "name": fd["name"],
                "label": fd["label"],
                "topic": fd["topic"],
                "cardinality": fd["cardinality"],
                "entropy_bits": fd["entropy_bits"],
                "codes": fd["codes"],
            })
        return jsonify({"metric": meta.metric_name,
                        "total_fields": len(out), "fields": out})

    @app.route("/provinces", methods=["GET"])
    def provinces_endpoint():
        return jsonify({"field": PROVINCE_FIELD,
                        "codes": meta.province_codes()})

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "metric": meta.metric_name,
                        "n_records": meta.n_records,
                        "fields": len(meta.fields)})

    @app.route("/", methods=["GET"])
    def index():
        return send_from_directory(WEB_DIR, "index.html")

    @app.route("/<path:path>", methods=["GET"])
    def static_files(path):
        return send_from_directory(WEB_DIR, path)

    return app


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")
    app = create_app()
    print(f"SILO Score API on http://{host}:{port}  (webpage at /)")
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
