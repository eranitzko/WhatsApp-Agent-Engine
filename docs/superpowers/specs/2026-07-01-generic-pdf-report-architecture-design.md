# Generic PDF Report Architecture — Design

## Problem

There are two independent, hand-rolled PDF generators:

- `app/reports/pdf_report.py` (`generate_pdf`) — used by `invoice_curator`. Hardcodes invoice-shaped columns (date, invoice#, vendor, description, amount(s)), owns Unicode font registration (DejaVu Sans) and bidi/RTL handling.
- `app/tools/accounting_export.py` (`generate_ledger_pdf`) — used by `family_accounting`. Hardcodes ledger-shaped output (a net-balances summary table, plus one date/description/side-A/side-B/comments table per counterparty pair). Imports `_bidi`/`_font`/`_register_font`/`_xml` from `pdf_report.py`, so font/bidi logic is shared at the function level, but every bit of table-drawing, column layout, and row logic is duplicated and re-implemented per report shape.

Every new blueprint that needs a PDF report today means writing a third bespoke drawing implementation. This is the thing being fixed: one blueprint-agnostic renderer, fed a generic (format, data) description, used by every blueprint present and future.

## Non-goals

- XLSX generation (`generate_excel`, `generate_ledger_xlsx`) is explicitly out of scope for this pass. The generic contract is designed so an XLSX renderer *could* consume the same `ReportSpec` later, but only a PDF renderer ships now.
- No change to the agent-facing tool schemas (`export_accounting_report`, `export_invoice_report`). The LLM's tool-call surface is unchanged — it still just calls these tools with the same parameters as today.
- No change to delivery (`deliver_files`, WhatsApp send, email) — only how the PDF bytes are produced.

## Who builds the report

Blueprint-specific server code (`InvoiceGenerator`, `AccountingGenerator`) — **not the LLM** — builds the report. The agent calls `export_accounting_report`/`export_invoice_report` exactly as it does today; the executor queries the DB, assembles a generic spec, and hands it to one shared renderer. The LLM never sees or emits raw report row data.

## The contract: `app/reports/spec.py`

Dataclasses, matching the existing `ReportData`/`ReportRow` convention already used in `app/reports/data.py`:

```python
@dataclass
class Run:
    text: str
    bold: bool = False
    color: str | None = None   # hex string, e.g. "#1a3c5e"; None = default

Cell = str | list[Run]   # plain string, or styled runs for char/word-level control

@dataclass
class Column:
    header: str
    type: Literal["text", "number"]   # drives alignment + bidi eligibility
    width_weight: float = 1.0          # relative column width within its section

@dataclass
class Row:
    cells: list[Cell]
    style: Literal["normal", "total", "flagged"] = "normal"

@dataclass
class TableSection:
    heading: str | None       # e.g. "Net Balances", "Eran — Sivan"; None = no heading
    columns: list[Column]
    rows: list[Row]
    totals_row: Row | None = None

@dataclass
class ReportSpec:
    title: str
    lang: Literal["en", "he"]
    generated_label: str        # e.g. "Generated: 2026-07-01 12:00 UTC"
    meta_lines: list[str]       # e.g. ["Period: July 2026", "Prepared by: Eran"]
    sections: list[TableSection]
```

**Note on scope of `sections`:** during brainstorming this was floated as "a table or a custom block (e.g. image grid)." For this pass, `sections` is `list[TableSection]` only — no generic non-table section type is introduced. Nothing currently needs a second content type, and inventing one now (before there's a concrete second use case) would be speculative generality. The invoice image appendix (see "Migration," step 2) stays outside the section model entirely, passed in via `render_pdf`'s `extra_flowables` parameter rather than as a section.

### Column `type` semantics

- `"text"` — free text that may contain Hebrew or English regardless of `spec.lang` (descriptions, names, vendors). Always bidi-processed before rendering, and wraps/aligns per `spec.lang`.
- `"number"` — already-formatted numeric/date strings (amounts, dates). Always LTR, right-aligned, never bidi-processed (bidi on a formatted number is a no-op but skipping it avoids wasted work).

### Row `style` semantics

- `"normal"` — default alternating-band rendering (current behavior).
- `"total"` — bold text, tinted background (replaces the ledger PDF's hardcoded totals-row styling).
- `"flagged"` — tinted background (replaces the invoice PDF's hardcoded flagged-row styling; the asterisk-in-date-string convention used by invoices stays the caller's choice of cell content, not a renderer concern).

### Cell-level `Run` styling

A cell is either a plain string (the common case) or a list of `Run`s for finer-grained control (bold a specific word, color a specific span) without needing a new row/column-level concept. This is intentionally minimal now (`bold`, `color`) but the `Run` dataclass can grow additional fields (italic, size) later without changing `Cell`'s shape.

## Formatting stays outside the renderer

`app/reports/formatting.py` (new, small) holds `format_date(d, date_format) -> str` and `format_currency(amount, currency_display) -> str` (sign-aware — a negative amount renders with a leading `-`), consolidated from the duplicated versions currently in `accounting_export.py`. It also holds `format_amount(amount, currency) -> str`, the multi-currency formatter used by `invoice_curator` (ILS gets the `₪` symbol, any other ISO currency code renders as a suffix; amounts here are never negative), consolidated from `pdf_report.py`'s existing `_fmt_amount`/`_fmt_ils`. Blueprint code calls these to turn raw `Decimal`/`date` values into final display strings *before* building `Row`/`Cell` objects. `render_pdf` never inspects a raw number or date — every cell it receives is already a string (or run list). This keeps the renderer honestly generic: it has no currency symbols, no date format knowledge, no business logic at all.

## The renderer: `app/reports/render_pdf.py`

One function: `render_pdf(spec: ReportSpec, extra_flowables: list | None = None) -> bytes`.

`extra_flowables` is a narrow, explicit escape hatch — a list of already-built ReportLab flowables (e.g. `PageBreak`, `Image`, `Table`) appended to the document's story after all sections, before the single internal `doc.build(story)` call. It exists for exactly one reason: the invoice image appendix (see "Migration," step 2) is built by *existing, untouched* code in `InvoiceGenerator` that must land in the *same* PDF document as the table, and `render_pdf` finalizes and returns bytes rather than an intermediate story list. This is not a generic "attach anything" mechanism to be used for report content — reports describe their content via `sections`; `extra_flowables` exists solely so the appendix (a fundamentally different content type — images, not row/column data — see the "Note on scope of `sections`" above) can land in the same document without inventing a new dependency (a PDF-merge library) or a second section type with no other use case.

Owns, as the single canonical implementation (replacing the current split-and-duplicated versions):
- DejaVu Sans TTF registration (regular + bold), Helvetica fallback with a logged warning if the font file is missing.
- Bidi reordering (`python-bidi`'s `get_display`), applied to every `"text"`-type cell and heading **unconditionally** — never gated on `spec.lang`. A Hebrew report can contain English vendor names; an English report can contain Hebrew descriptions (this is the exact bug found and fixed earlier today in the ledger PDF — bidi must be a property of the content, not the report). `get_display` is a no-op on pure-LTR text, so this is always safe.
- Column/row visual order reversal when `spec.lang == "he"` (RTL layout), applied uniformly per section based on each section's own columns — not hardcoded per report shape.
- XML-escaping applied *after* bidi reordering, never before (the exact ordering bug found and fixed in `accounting_export.py`'s `_t()` today — escaping first corrupts any text containing a literal `"`, `&`, `<`, or `>` once bidi reorders the escaped entity's characters individually).
- Title/meta header layout (mirrors both existing reports' header block: title + generated timestamp + period/author lines, RTL-aware corner placement).
- Section heading, table (with `repeatRows=1` for pagination), and totals-row rendering, using `Column.width_weight` to size columns within each section's available width.

## Migration

1. Build `spec.py`, `formatting.py`, `render_pdf.py` and their unit tests (fixtures only — no blueprint changes yet).
2. Migrate `InvoiceGenerator.build_pdf` to build a `ReportSpec` (one `TableSection`, `"flagged"` row style for flagged invoices), keep its existing (untouched) appendix-building code but have it return a list of flowables instead of appending to its own `story`, and pass that list as `render_pdf`'s `extra_flowables` when `attach_images=True`. Once this passes and is verified, delete the invoice-specific table-building, font-registration, and bidi code from `pdf_report.py` — its appendix-building code stays (unchanged) but now returns flowables to be handed to `render_pdf` rather than building its own document.
   - **This migration also fixes an existing latent bug, not just moves code.** `pdf_report.py`'s current `_t()` (used for the title/period/author header text) does `_bidi(_xml(text)) if rtl else _xml(text)` — the exact same wrong ordering (escape before bidi, and gated on `rtl`) found and fixed in the ledger PDF earlier today. It hasn't visibly failed yet only because no title/period/author string has both been in Hebrew mode and contained a literal `"`, `&`, `<`, or `>`. `render_pdf`'s unconditional, correctly-ordered bidi handling (see "The renderer" section above) replaces this outright — no separate fix needed beyond doing the migration per this spec.
3. Migrate `AccountingGenerator.build_pdf` (via `generate_ledger_pdf`) to build a `ReportSpec` (one `TableSection` for net balances, one per counterparty pair, `"total"` row style for each section's totals row) and call `render_pdf` (no `extra_flowables` — the ledger report never has an image appendix). Delete `generate_ledger_pdf`'s bespoke drawing, font-registration, and bidi code once this passes and is verified.
4. Once both blueprints are migrated, `pdf_report.py` contains only the (relocated, untouched) appendix-flowable-building function — no table drawing, no font/bidi code, since that all now lives solely in `render_pdf.py`. No dead code left behind, no permanent fallback path.

Verification at each migration step follows the same method used earlier today: generate a real PDF against production-shaped data inside the actual container (real DejaVu Sans + `python-bidi` installed), inspect via the Read tool's PDF support, and confirm against the pre-migration output for the same input data before deleting the old code path.

## Testing

- `tests/test_render_pdf.py` (new): builds small `ReportSpec` fixtures directly — an English-only table, a Hebrew table with an embedded English word, a table with a `"total"`-styled row, a table with a `"flagged"`-styled row, an RTL section with mixed content — and asserts non-empty `%PDF`-prefixed bytes. Bidi/RTL correctness is checked by extracting text (via the same PDF-reading approach used manually this session) and confirming character order, not just byte presence.
- `tests/test_formatting.py` (new): unit tests for `format_date`/`format_currency`, including the negative-amount sign case.
- Existing `test_export_tool.py` tests for `InvoiceGenerator`/`AccountingGenerator` are updated to assert on the `ReportSpec` shape they produce (section count, column headers, row styles) rather than only checking output byte length, so a future regression in spec construction is caught even if `render_pdf` itself is correct.
