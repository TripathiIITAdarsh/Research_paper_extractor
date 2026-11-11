"""
Flask UI: one search box -> four labeled tables (OpenAlex, Semantic Scholar, arXiv, Crossref)
          + a merged top-N panel with selectable ranking criterion.
Run:  python app.py   -> http://127.0.0.1:5000
"""
import io
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, request, jsonify, send_file

from exporter import build_export_workbook
from sources import SOURCES

app = Flask(__name__)

POOL_SIZE_DEFAULT = 50
POOL_SIZE_MIN = 5
POOL_SIZE_MAX = 200   # practical ceiling (OpenAlex per-page is 200; some APIs cap lower)
TOP_N = 100           # default merged panel size (client can override)


def _title_key(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()[:120]


def _dedupe_key(p: dict) -> str:
    if p.get("doi"):
        return "doi:" + p["doi"]
    return "t:" + _title_key(p.get("title") or "")


def _merge_record(a: dict, b: dict) -> dict:
    """Combine two records that refer to the same paper."""
    out = dict(a)
    # citations: max of non-None
    cites = [x for x in (a.get("citations"), b.get("citations")) if x is not None]
    out["citations"] = max(cites) if cites else None
    # year: first non-None
    out["year"] = a.get("year") or b.get("year")
    # authors: longer list wins
    out["authors"] = a.get("authors") if len(a.get("authors") or []) >= len(b.get("authors") or []) else b.get("authors")
    # abstract: longer wins
    aa, ab = a.get("abstract") or "", b.get("abstract") or ""
    out["abstract"] = aa if len(aa) >= len(ab) else ab
    # title: longer wins (fuller punctuation/subtitles)
    at, bt = a.get("title") or "", b.get("title") or ""
    out["title"] = at if len(at) >= len(bt) else bt
    # doi + url: prefer non-None
    out["doi"] = a.get("doi") or b.get("doi")
    out["url"] = a.get("url") or b.get("url")
    # journal: prefer the one that isn't the "arXiv (preprint)" placeholder
    ja, jb = a.get("journal") or "", b.get("journal") or ""
    def _is_preprint(j): return j.lower().startswith("arxiv") if j else True
    if ja and not _is_preprint(ja):
        out["journal"] = ja
    elif jb and not _is_preprint(jb):
        out["journal"] = jb
    else:
        out["journal"] = ja or jb or None
    # sources: union
    sa = set(a.get("sources") or ([a["source"]] if a.get("source") else []))
    sb = set(b.get("sources") or ([b["source"]] if b.get("source") else []))
    out["sources"] = sorted(sa | sb)
    # rank_in_source: merged dict
    ra = dict(a.get("rank_in_source") or {})
    rb = dict(b.get("rank_in_source") or {})
    ra.update({k: min(ra[k], rb[k]) if k in ra else rb[k] for k in rb})
    out["rank_in_source"] = ra
    return out


def build_merged_pool(per_source: dict[str, list[dict]]) -> list[dict]:
    """Dedupe across sources, attaching per-source rank for RRF."""
    bucket: dict[str, dict] = {}
    for src_key, rows in per_source.items():
        for i, row in enumerate(rows):
            enriched = dict(row)
            enriched["sources"] = [src_key]
            enriched["rank_in_source"] = {src_key: i + 1}  # 1-indexed
            k = _dedupe_key(enriched)
            if k in bucket:
                bucket[k] = _merge_record(bucket[k], enriched)
            else:
                bucket[k] = enriched
    return list(bucket.values())


@app.route("/")
def index():
    return render_template(
        "index.html",
        sources=[(k, v[0]) for k, v in SOURCES.items()],
    )


@app.route("/export", methods=["POST"])
def export():
    """
    Build the .xlsx in memory and stream it as a download.
    This works on ephemeral hosts (HF Spaces free tier) — no disk writes.
    """
    payload = request.get_json() or {}
    query = (payload.get("query") or "").strip() or "search"
    merged = payload.get("merged") or []
    per_source_in = payload.get("per_source") or {}
    per_source = {
        label: {
            "rows": info.get("rows") or [],
            "error": info.get("error"),
            "count": len(info.get("rows") or []),
        }
        for label, info in per_source_in.items()
    }
    try:
        wb, filename = build_export_workbook(query, merged, per_source)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return send_file(
            buf,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.route("/search")
def search():
    query = (request.args.get("q") or "").strip()
    try:
        pool_size = int(request.args.get("limit") or POOL_SIZE_DEFAULT)
    except ValueError:
        pool_size = POOL_SIZE_DEFAULT
    pool_size = max(POOL_SIZE_MIN, min(pool_size, POOL_SIZE_MAX))
    if not query:
        return jsonify({"error": "empty query"}), 400

    per_source_full: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}

    def run(key):
        label, fn = SOURCES[key]
        return key, fn(query, limit=pool_size)

    with ThreadPoolExecutor(max_workers=len(SOURCES)) as ex:
        futures = {ex.submit(run, k): k for k in SOURCES.keys()}
        for fut in as_completed(futures):
            k = futures[fut]
            try:
                _, rows = fut.result()
                per_source_full[k] = rows
            except Exception as e:
                errors[k] = f"{type(e).__name__}: {e}"
                per_source_full[k] = []

    merged = build_merged_pool(per_source_full)  # send full deduped pool; client slices

    return jsonify({
        "query": query,
        "sources": [
            {
                "key": k,
                "label": SOURCES[k][0],
                "rows": per_source_full.get(k, []),   # no slice: show everything we fetched
                "fetched": len(per_source_full.get(k, [])),
                "requested": pool_size,
                "error": errors.get(k),
            }
            for k in SOURCES.keys()
        ],
        "merged": merged,
        "top_n": TOP_N,
        "pool_size": pool_size,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    host = os.environ.get("HOST", "127.0.0.1" if debug else "0.0.0.0")
    if debug:
        app.run(host=host, port=port, debug=True)
    else:
        try:
            from waitress import serve
            shown = "localhost" if host in ("0.0.0.0", "::") else host
            print(f"[paper-agent] serving on http://{shown}:{port}  (bound to {host})")
            serve(app, host=host, port=port, threads=8)
        except ImportError:
            # Fallback: Flask's dev server with debug off (not ideal but works)
            app.run(host=host, port=port, debug=False)
