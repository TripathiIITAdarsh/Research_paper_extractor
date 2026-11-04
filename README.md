# Paper Agent

Queries six free academic APIs (OpenAlex, Semantic Scholar, arXiv, Crossref, Europe PMC, INSPIRE-HEP) in parallel, deduplicates across sources, and lets you rank and export the merged list to Excel.

Built for a review paper on magnetic tunnel junctions (DFT + NEGF). Works for any academic topic.

## Optional environment variables

Set these in the Space's **Settings → Variables and secrets**:

- `CORE_API_KEY` — adds CORE (240M open-access papers) as a seventh source. Get a free key at [core.ac.uk/services/api](https://core.ac.uk/services/api).
- `GEMINI_API_KEY` — enables LLM relevance scoring (currently disabled in the UI).
