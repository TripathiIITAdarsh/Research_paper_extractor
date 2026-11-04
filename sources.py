"""
Source adapters for OpenAlex, Semantic Scholar, arXiv, Crossref, Europe PMC,
INSPIRE-HEP, and (if CORE_API_KEY is set) CORE.
Each returns a list[dict] with a common shape:
    {title, year, authors, doi, citations, abstract, journal, url, source}
"""
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

import requests

MAILTO = "mananpapneja@gmail.com"
UA = {"User-Agent": f"paper-agent/0.1 (mailto:{MAILTO})"}
CORE_API_KEY = os.environ.get("CORE_API_KEY", "").strip()


def _get(url, params=None, timeout=30, retries=3, backoff=1.5, headers=None):
    last = None
    h = dict(UA)
    if headers:
        h.update(headers)
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers=h)
            if r.status_code in (502, 503, 504, 429):
                last = requests.HTTPError(f"{r.status_code}")
                time.sleep(backoff * (i + 1))
                continue
            r.raise_for_status()
            return r
        except requests.exceptions.RequestException as e:
            last = e
            time.sleep(backoff * (i + 1))
    raise last


def _norm_doi(doi):
    if not doi:
        return None
    return doi.lower().replace("https://doi.org/", "").replace("http://doi.org/", "").strip()


# ----------------- OpenAlex -----------------
def fetch_openalex(query, limit=25):
    r = _get(
        "https://api.openalex.org/works",
        params={
            "search": query,
            "per-page": limit,
            "mailto": MAILTO,
            "select": "id,title,publication_year,authorships,doi,cited_by_count,abstract_inverted_index,primary_location,type",
        },
    )
    out = []
    for w in r.json().get("results", []):
        inv = w.get("abstract_inverted_index")
        abstract = None
        if inv:
            flat = sorted((pos, word) for word, poss in inv.items() for pos in poss)
            abstract = " ".join(w for _, w in flat)
        authors = [
            a["author"]["display_name"]
            for a in w.get("authorships", [])
            if a.get("author", {}).get("display_name")
        ][:5]
        primary = w.get("primary_location") or {}
        url = primary.get("landing_page_url") or w.get("id")
        journal = ((primary.get("source") or {}).get("display_name")) or None
        out.append({
            "title": w.get("title") or "",
            "year": w.get("publication_year"),
            "authors": authors,
            "doi": _norm_doi(w.get("doi")),
            "citations": w.get("cited_by_count"),
            "abstract": abstract,
            "journal": journal,
            "url": url,
            "source": "openalex",
        })
    return out


# ----------------- arXiv -----------------
def fetch_arxiv(query, limit=25):
    q = urllib.parse.quote(query)
    url = f"http://export.arxiv.org/api/query?search_query=all:{q}&start=0&max_results={limit}&sortBy=relevance"
    r = _get(url)
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
        jref_el = entry.find("arxiv:journal_ref", ns)
        journal = jref_el.text.strip() if (jref_el is not None and jref_el.text) else "arXiv (preprint)"
        out.append({
            "title": title,
            "year": year,
            "authors": authors,
            "doi": _norm_doi(doi),
            "citations": None,
            "abstract": summary,
            "journal": journal,
            "url": link,
            "source": "arxiv",
        })
    return out


# ----------------- Semantic Scholar -----------------
def fetch_semantic_scholar(query, limit=25):
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {
        "query": query,
        "limit": limit,
        "fields": "title,year,authors,externalIds,citationCount,abstract,openAccessPdf,url,venue,journal,publicationVenue",
    }
    r = _get(url, params=params, timeout=40)
    out = []
    for p in r.json().get("data", []):
        ids = p.get("externalIds") or {}
        authors = [a.get("name") for a in (p.get("authors") or []) if a.get("name")][:5]
        journal = (
            (p.get("publicationVenue") or {}).get("name")
            or (p.get("journal") or {}).get("name")
            or p.get("venue")
            or None
        )
        out.append({
            "title": p.get("title") or "",
            "year": p.get("year"),
            "authors": authors,
            "doi": _norm_doi(ids.get("DOI")),
            "citations": p.get("citationCount"),
            "abstract": p.get("abstract"),
            "journal": journal,
            "url": p.get("url"),
            "source": "semanticscholar",
        })
    return out


# ----------------- Crossref -----------------
def fetch_crossref(query, limit=25):
    url = "https://api.crossref.org/works"
    params = {
        "query": query,
        "rows": limit,
        "select": "DOI,title,author,issued,is-referenced-by-count,abstract,URL,container-title,publisher,type",
    }
    r = _get(url, params=params, headers={"User-Agent": f"paper-agent/0.1 (mailto:{MAILTO})"})
    out = []
    for it in r.json().get("message", {}).get("items", []):
        title = " ".join(it.get("title") or []).strip()
        issued = (it.get("issued") or {}).get("date-parts") or [[None]]
        year = issued[0][0] if issued and issued[0] else None
        authors = []
        for a in (it.get("author") or [])[:5]:
            n = " ".join(x for x in [a.get("given"), a.get("family")] if x)
            if n:
                authors.append(n)
        abstract = it.get("abstract")
        if abstract:
            abstract = re.sub(r"<[^>]+>", "", abstract).strip()
        containers = it.get("container-title") or []
        journal = containers[0] if containers else (it.get("publisher") or None)
        out.append({
            "title": title,
            "year": year,
            "authors": authors,
            "doi": _norm_doi(it.get("DOI")),
            "citations": it.get("is-referenced-by-count"),
            "abstract": abstract,
            "journal": journal,
            "url": it.get("URL"),
            "source": "crossref",
        })
    return out


# ----------------- Europe PMC -----------------
def fetch_europepmc(query, limit=25):
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    params = {"query": query, "resultType": "lite", "format": "json", "pageSize": limit}
    r = _get(url, params=params)
    out = []
    for it in r.json().get("resultList", {}).get("result", []):
        authors = []
        if it.get("authorString"):
            authors = [a.strip() for a in it["authorString"].split(",")][:5]
        year = None
        if it.get("pubYear"):
            try:
                year = int(it["pubYear"])
            except ValueError:
                pass
        journal = it.get("journalTitle") or (it.get("journalInfo") or {}).get("journal", {}).get("title") or None
        out.append({
            "title": it.get("title") or "",
            "year": year,
            "authors": authors,
            "doi": _norm_doi(it.get("doi")),
            "citations": it.get("citedByCount"),
            "abstract": None,  # lite endpoint doesn't include it; could switch to resultType=core
            "journal": journal,
            "url": f"https://europepmc.org/abstract/{it.get('source','MED')}/{it.get('id','')}" if it.get("id") else None,
            "source": "europepmc",
        })
    return out


# ----------------- INSPIRE-HEP -----------------
def fetch_inspire(query, limit=25):
    url = "https://inspirehep.net/api/literature"
    params = {"q": query, "size": limit, "format": "json", "fields": "titles,authors,publication_info,citation_count,arxiv_eprints,dois,abstracts"}
    r = _get(url, params=params)
    out = []
    for h in r.json().get("hits", {}).get("hits", []):
        m = h.get("metadata", {})
        titles = m.get("titles") or []
        title = titles[0].get("title") if titles else ""
        authors = [a.get("full_name") for a in (m.get("authors") or [])[:5] if a.get("full_name")]
        pub = (m.get("publication_info") or [{}])[0]
        year = pub.get("year")
        journal = pub.get("journal_title") or None
        dois = m.get("dois") or []
        doi = dois[0].get("value") if dois else None
        arxiv_eps = m.get("arxiv_eprints") or []
        arxiv_id = arxiv_eps[0].get("value") if arxiv_eps else None
        abstracts = m.get("abstracts") or []
        abstract = abstracts[0].get("value") if abstracts else None
        url_ = f"https://inspirehep.net/literature/{h.get('id')}" if h.get("id") else None
        if arxiv_id and not url_:
            url_ = f"https://arxiv.org/abs/{arxiv_id}"
        out.append({
            "title": title or "",
            "year": year,
            "authors": authors,
            "doi": _norm_doi(doi),
            "citations": m.get("citation_count"),
            "abstract": abstract,
            "journal": journal or "arXiv (preprint)",
            "url": url_,
            "source": "inspire",
        })
    return out


# ----------------- CORE (needs free API key) -----------------
def fetch_core(query, limit=25):
    if not CORE_API_KEY:
        raise RuntimeError("CORE_API_KEY not set (get a free key at core.ac.uk/services/api)")
    url = "https://api.core.ac.uk/v3/search/works"
    headers = {"Authorization": f"Bearer {CORE_API_KEY}"}
    r = _get(url, params={"q": query, "limit": limit}, headers=headers)
    out = []
    for w in r.json().get("results", []):
        authors = [a.get("name") for a in (w.get("authors") or [])[:5] if a.get("name")]
        year = w.get("yearPublished")
        journal = None
        pub = w.get("publisher")
        jr = w.get("journals") or []
        if jr:
            # CORE represents journals as list of {title: ...} or ids
            first = jr[0]
            if isinstance(first, dict):
                journal = first.get("title")
        if not journal and pub:
            journal = pub
        out.append({
            "title": w.get("title") or "",
            "year": year,
            "authors": authors,
            "doi": _norm_doi(w.get("doi")),
            "citations": w.get("citationCount"),
            "abstract": w.get("abstract"),
            "journal": journal,
            "url": w.get("downloadUrl") or w.get("sourceFulltextUrls", [None])[0] or w.get("doi"),
            "source": "core",
        })
    return out


SOURCES = {
    "openalex": ("OpenAlex", fetch_openalex),
    "semanticscholar": ("Semantic Scholar", fetch_semantic_scholar),
    "arxiv": ("arXiv", fetch_arxiv),
    "crossref": ("Crossref", fetch_crossref),
    "europepmc": ("Europe PMC", fetch_europepmc),
    "inspire": ("INSPIRE-HEP", fetch_inspire),
}
if CORE_API_KEY:
    SOURCES["core"] = ("CORE", fetch_core)
