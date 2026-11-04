"""
Write a search result to an .xlsx file in exports/.

Workbook layout:
    Summary       -> query, timestamp, source counts, totals
    Merged        -> deduped + ranked top N (cols include Gemini score when present)
    <SourceName>  -> one sheet per source (full rows the API returned)
"""
import re
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

EXPORT_DIR = Path(__file__).parent / "exports"


def _slug(s: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return (s[:maxlen] or "search").strip("-")


def _authors(a):
    return ", ".join(a or [])


def _write_header(ws, columns):
    for i, c in enumerate(columns, 1):
        cell = ws.cell(row=1, column=i, value=c)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2B5BD7")
        cell.alignment = Alignment(vertical="center")


def _autowidth(ws, columns, maxw=60):
    widths = [len(c) for c in columns]
    for row in ws.iter_rows(min_row=2, values_only=True):
        for i, v in enumerate(row):
            if v is None:
                continue
            widths[i] = max(widths[i], min(len(str(v)), maxw))
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w + 2


def _freeze_and_filter(ws, cols_n):
    ws.freeze_panes = "A2"
    last_col = get_column_letter(cols_n)
    ws.auto_filter.ref = f"A1:{last_col}1"


def _write_source_sheet(ws, rows):
    cols = ["#", "Year", "Citations", "Title", "Journal", "Authors", "DOI", "URL", "Abstract"]
    _write_header(ws, cols)
    for i, r in enumerate(rows, 1):
        ws.append([
            i,
            r.get("year"),
            r.get("citations"),
            r.get("title") or "",
            r.get("journal") or "",
            _authors(r.get("authors")),
            r.get("doi") or "",
            r.get("url") or "",
            (r.get("abstract") or "")[:4000],
        ])
    _autowidth(ws, cols)
    _freeze_and_filter(ws, len(cols))


def _write_merged_sheet(ws, rows):
    cols = ["#", "Year", "Citations", "C/yr",
            "Title", "Journal", "Authors", "DOI", "Sources", "URL", "Abstract"]
    _write_header(ws, cols)
    now_year = datetime.now().year
    for i, r in enumerate(rows, 1):
        cites = r.get("citations")
        year = r.get("year")
        cpy = (cites / max(1, now_year - year + 1)) if (cites is not None and year) else None
        ws.append([
            i, year, cites,
            round(cpy, 2) if cpy is not None else None,
            r.get("title") or "",
            r.get("journal") or "",
            _authors(r.get("authors")),
            r.get("doi") or "",
            ", ".join(r.get("sources") or []),
            r.get("url") or "",
            (r.get("abstract") or "")[:4000],
        ])
    _autowidth(ws, cols)
    _freeze_and_filter(ws, len(cols))


def _write_summary_sheet(ws, query: str, per_source: dict[str, dict], merged_count: int):
    ws.append(["Paper search export"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([])
    ws.append(["Query", query])
    ws.append(["Timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    ws.append(["Merged unique papers", merged_count])
    ws.append([])
    ws.append(["Source", "Papers returned", "Error"])
    for cell in ws[7]:
        cell.font = Font(bold=True)
    for label, info in per_source.items():
        ws.append([label, info["count"], info.get("error") or ""])
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 20
    ws.column_dimensions["C"].width = 40


def build_export_workbook(query: str, merged: list[dict], per_source: dict[str, dict]):
    """
    Build the openpyxl Workbook in memory. Returns (workbook, suggested_filename).
    Caller decides whether to save it to disk or stream it as a download.
    """
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    filename = f"{stamp}__{_slug(query)}.xlsx"

    wb = Workbook()
    wb.remove(wb.active)

    summary = wb.create_sheet("Summary")
    _write_summary_sheet(summary, query, per_source, len(merged))

    merged_ws = wb.create_sheet("Merged")
    _write_merged_sheet(merged_ws, merged)

    for label, info in per_source.items():
        safe = re.sub(r"[^A-Za-z0-9 _-]", "", label)[:31] or "Source"
        ws = wb.create_sheet(safe)
        _write_source_sheet(ws, info["rows"])

    return wb, filename


def write_export(query: str, merged: list[dict], per_source: dict[str, dict]) -> str:
    """Legacy helper: write the workbook to exports/ and return the path."""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    wb, filename = build_export_workbook(query, merged, per_source)
    path = EXPORT_DIR / filename
    wb.save(path)
    return str(path)
