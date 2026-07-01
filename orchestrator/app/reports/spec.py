"""Generic report specification — the data blueprint code builds and hands to
render_pdf().

Every blueprint (invoice_curator, family_accounting, and any future one)
describes its report as a ReportSpec: an ordered list of TableSections, each
with typed columns and already-formatted string cells. render_pdf()
(app/reports/render_pdf.py) knows nothing about invoices or ledgers — only
how to lay out sections, tables, rows, and cells. See
docs/superpowers/specs/2026-07-01-generic-pdf-report-architecture-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ColumnType = Literal["text", "number"]
RowStyle = Literal["normal", "total", "flagged"]


@dataclass
class Run:
    """A styled span of text within a cell, for character/word-level styling."""
    text: str
    bold: bool = False
    color: str | None = None   # hex string e.g. "#1a3c5e"; None = default


Cell = str | list[Run]


@dataclass
class Column:
    header: str
    type: ColumnType = "text"
    width_weight: float = 1.0


@dataclass
class Row:
    cells: list[Cell]
    style: RowStyle = "normal"


@dataclass
class TableSection:
    columns: list[Column]
    rows: list[Row]
    heading: str | None = None
    totals_row: Row | None = None
    empty_message: str | None = None   # shown instead of an empty table when rows == []


@dataclass
class ReportSpec:
    title: str
    lang: Literal["en", "he"]
    generated_label: str
    sections: list[TableSection]
    meta_lines: list[str] = field(default_factory=list)
