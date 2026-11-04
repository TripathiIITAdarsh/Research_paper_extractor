"""
Gemini relevance scorer. Takes a list of {title, abstract} + a topic description,
returns [{score: 0-10, reason: "..."}].

Does NOT fetch new papers (zero hallucination risk). Only judges the ones you pass.
Needs GEMINI_API_KEY env var.
"""
import json
import os
import re
import time
from pathlib import Path

import requests


def _load_key() -> str:
    # Env var wins (so the HF Spaces secret is authoritative when deployed).
    # Falls back to a local untracked file for local development.
    env_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_key:
        return env_key
    f = Path(__file__).parent / ".gemini_key"
    if f.exists():
        return f.read_text(encoding="utf-8").strip()
    return ""


GEMINI_API_KEY = _load_key()
MODEL = "gemini-flash-lite-latest"


def available() -> bool:
    return bool(GEMINI_API_KEY)


def score_papers(papers: list[dict], topic: str) -> list[dict]:
    """
    papers: list of {"title": str, "abstract": str|None}
    topic: free-text topic description, e.g. "MTJ with DFT+NEGF on Heusler compounds"
    Returns parallel list of {"score": int, "reason": str}.
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")
    if not papers:
        return []

    items = []
    for i, p in enumerate(papers):
        t = (p.get("title") or "").strip()
        a = (p.get("abstract") or "").strip()
        items.append(f'[{i}] title: {t}\n    abstract: {a[:800] if a else "(no abstract)"}')
    block = "\n".join(items)

    prompt = (
        f"You are rating the relevance of research papers to this topic:\n"
        f"TOPIC: {topic}\n\n"
        f"For each paper, output a line of JSON: "
        f'{{"i": <index>, "score": <0-10 int>, "reason": "<<=15 words>"}}\n'
        f"Scoring guide: 10 = directly on-topic, 7-9 = closely related, "
        f"4-6 = adjacent topic, 1-3 = tangential, 0 = off-topic.\n"
        f"Output ONE JSON line per paper, in order, nothing else.\n\n"
        f"PAPERS:\n{block}"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={GEMINI_API_KEY}"
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    last_err = None
    data = None
    for attempt in range(3):
        try:
            r = requests.post(url, json=body, timeout=90)
            if r.status_code in (503, 429, 500):
                last_err = requests.HTTPError(f"{r.status_code} {r.reason}")
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            break
        except requests.exceptions.RequestException as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    if data is None:
        raise last_err or RuntimeError("Gemini request failed")
    text = ""
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise RuntimeError(f"unexpected Gemini response: {json.dumps(data)[:300]}")

    results = [{"score": None, "reason": ""} for _ in papers]
    for line in text.strip().splitlines():
        line = line.strip().lstrip("`")
        if not line.startswith("{"):
            # try to extract an inline JSON object
            m = re.search(r"\{.*\}", line)
            if not m:
                continue
            line = m.group(0)
        try:
            obj = json.loads(line)
            i = int(obj.get("i", -1))
            if 0 <= i < len(results):
                results[i] = {
                    "score": int(obj.get("score")) if obj.get("score") is not None else None,
                    "reason": str(obj.get("reason", ""))[:200],
                }
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    return results
