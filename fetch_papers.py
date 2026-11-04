"""
POC: fetch papers for a category from OpenAlex + arXiv, dedupe, print.

Usage:
    python fetch_papers.py "Heusler MTJ DFT NEGF"
    python fetch_papers.py --category heusler
"""
import argparse
import io
import re
import sys
import time
import urllib.parse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
from dataclasses import dataclass, field
from typing import Optional

import requests
import xml.etree.ElementTree as ET


def _get(url, params=None, timeout=30, retries=3, backoff=2.0):
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code in (502, 503, 504, 429):
                last = requests.HTTPError(f"{r.status_code} on attempt {i+1}")
                time.sleep(backoff * (i + 1))
                continue
            r.raise_for_status()
            return r
        except requests.exceptions.RequestException as e:
            last = e
            time.sleep(backoff * (i + 1))
    raise last

MAILTO = "mananpapneja@gmail.com"  # polite pool for OpenAlex

CATEGORIES = {
    "conventional":   "conventional magnetic alloy MTJ DFT NEGF tunnel magnetoresistance",
    "heusler":        "Heusler half-Heusler MTJ DFT NEGF spin transport",
    "2d":             "2D magnetic material MTJ DFT NEGF van der Waals tunnel junction",
    "antiferro":      "antiferromagnetic MTJ DFT NEGF tunnel junction spin transport",
    "oxides":         "oxide multiferroic MTJ DFT NEGF tunnel magnetoresistance",
    "topological":    "magnetic topological material MTJ DFT NEGF spin transport",
    "quantum":        "quantum material MTJ DFT NEGF spin tunnel junction",
}


@dataclass
class Paper:
    title: str
    year: Optional[int]
    authors: list[str] = field(default_factory=list)
    doi: Optional[str] = None
    citations: Optional[int] = None
    abstract: Optional[str] = None
    source: str = ""
    url: Optional[str] = None

    def norm_key(self) -> str:
        if self.doi:
            return "doi:" + self.doi.lower().strip()
        t = re.sub(r"[^a-z0-9]+", " ", (self.title or "").lower()).strip()
        return "t:" + t[:120]


def fetch_openalex(query: str, per_page: int = 25) -> list[Paper]:
    url = "https://api.openalex.org/works"
    params = {
        "search": query,
        "per-page": per_page,
        "mailto": MAILTO,
        "select": "id,title,publication_year,authorships,doi,cited_by_count,abstract_inverted_index",
    }
    r = _get(url, params=params, timeout=30)
    out = []
    for w in r.json().get("results", []):
        abstract = None
        inv = w.get("abstract_inverted_index")
        if inv:
            flat = sorted(
                ((pos, word) for word, positions in inv.items() for pos in positions)
            )
            abstract = " ".join(word for _, word in flat)
        authors = [
            a["author"]["display_name"]
            for a in w.get("authorships", [])
            if a.get("author", {}).get("display_name")
        ][:5]
        out.append(
            Paper(
                title=w.get("title") or "",
                year=w.get("publication_year"),
                authors=authors,
                doi=(w.get("doi") or "").replace("https://doi.org/", "") or None,
                citations=w.get("cited_by_count"),
                abstract=abstract,
                source="openalex",
                url=w.get("id"),
            )
        )
    return out


def fetch_arxiv(query: str, max_results: int = 25) -> list[Paper]:
    base = "http://export.arxiv.org/api/query"
    q = urllib.parse.quote(query)
    url = f"{base}?search_query=all:{q}&start=0&max_results={max_results}&sortBy=relevance"
    r = _get(url, timeout=30)
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    root = ET.fromstring(r.text)
    out = []
    for entry in root.findall("a:entry", ns):
        title = (entry.findtext("a:title", "", ns) or "").strip().replace("\n", " ")
        summary = (entry.findtext("a:summary", "", ns) or "").strip().replace("\n", " ")
        published = entry.findtext("a:published", "", ns) or ""
        year = int(published[:4]) if published[:4].isdigit() else None
        link = entry.findtext("a:id", "", ns)
        authors = [a.findtext("a:name", "", ns) for a in entry.findall("a:author", ns)][:5]
        doi_el = entry.find("arxiv:doi", ns)
        doi = doi_el.text if doi_el is not None else None
        out.append(
            Paper(
                title=title,
                year=year,
                authors=authors,
                doi=doi,
                citations=None,
                abstract=summary,
                source="arxiv",
                url=link,
            )
        )
    return out


def dedupe(papers: list[Paper]) -> list[Paper]:
    seen: dict[str, Paper] = {}
    for p in papers:
        k = p.norm_key()
        if k not in seen:
            seen[k] = p
        else:
            # merge: keep OpenAlex (has citations) but add arXiv URL if useful
            cur = seen[k]
            if cur.citations is None and p.citations is not None:
                cur.citations = p.citations
            if not cur.doi and p.doi:
                cur.doi = p.doi
    return list(seen.values())


def rank(papers: list[Paper]) -> list[Paper]:
    def score(p: Paper):
        c = p.citations or 0
        y = p.year or 0
        return (c, y)
    return sorted(papers, key=score, reverse=True)


def to_markdown(papers: list[Paper], title: str) -> str:
    lines = [f"# {title}", "", f"_{len(papers)} papers after dedupe_", ""]
    lines.append("| # | Year | Cites | Title | Authors | DOI / arXiv |")
    lines.append("|---|------|-------|-------|---------|-------------|")
    for i, p in enumerate(papers, 1):
        auth = ", ".join(p.authors[:3]) + (" et al." if len(p.authors) > 3 else "")
        ref = p.doi or (p.url or "").replace("http://arxiv.org/abs/", "arXiv:")
        t = p.title.replace("|", "/")[:120]
        lines.append(f"| {i} | {p.year or ''} | {p.citations if p.citations is not None else ''} | {t} | {auth} | {ref} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", help="free-text query, or omit if using --category")
    ap.add_argument("--category", choices=CATEGORIES.keys())
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    if args.category:
        query = CATEGORIES[args.category]
        title = f"Category: {args.category}"
    elif args.query:
        query = args.query
        title = f"Query: {query}"
    else:
        ap.error("supply a query or --category")

    print(f"[*] Query: {query}", file=sys.stderr)
    print("[*] Fetching OpenAlex...", file=sys.stderr)
    try:
        oa = fetch_openalex(query)
        print(f"    got {len(oa)}", file=sys.stderr)
    except Exception as e:
        print(f"    FAILED: {e}", file=sys.stderr)
        oa = []
    time.sleep(0.5)
    print("[*] Fetching arXiv...", file=sys.stderr)
    try:
        ax = fetch_arxiv(query)
        print(f"    got {len(ax)}", file=sys.stderr)
    except Exception as e:
        print(f"    FAILED: {e}", file=sys.stderr)
        ax = []

    merged = dedupe(oa + ax)
    merged = rank(merged)[: args.limit]
    print(to_markdown(merged, title))


if __name__ == "__main__":
    main()
