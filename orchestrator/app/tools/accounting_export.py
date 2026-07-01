"""XLSX ledger export — supports filter_phone and report format config."""

from __future__ import annotations

import io
from datetime import date as date_type
from decimal import Decimal

import openpyxl
from openpyxl.styles import Font
from sqlalchemy import or_

from app.db.session import SessionLocal
from app.db.models import LedgerEntry

_DATE_FORMATS = {
    "DD/MM/YYYY": lambda d: d.strftime("%d/%m/%Y") if d else "",
    "YYYY-MM-DD": lambda d: d.isoformat() if d else "",
    "DD MMM YYYY": lambda d: d.strftime("%d %b %Y") if d else "",
}


def _phone_to_name_from_db(db, group_jid: str, phones: set[str] | None = None) -> dict[str, str]:
    """Best display name per phone.

    Priority: HouseholdMember.display_name / UserProfile.display_name (set via
    the admin "management page") > GroupParticipant.admin_name/push_name
    (derived from the WhatsApp conversation) > raw phone.

    `phones` should include every phone appearing in the report (e.g. from
    ledger entries) so names resolve even for people not registered as a
    GroupParticipant of this group.  GroupParticipant.is_household participants
    keep the collective "Parents" label regardless of any individual display
    name — that grouping is intentional, not a missing name.
    """
    from app.db.models import GroupParticipant, HouseholdMember, UserProfile

    rows = db.query(GroupParticipant).filter_by(group_jid=group_jid).all()
    household = {r.phone for r in rows if r.is_household}
    result: dict[str, str] = {}
    for r in rows:
        name = r.admin_name or r.push_name or r.phone
        result[r.phone] = "Parents" if r.phone in household else name

    all_phones = set(result.keys()) | (phones or set())
    if all_phones:
        for profile in db.query(UserProfile).filter(UserProfile.phone.in_(all_phones)).all():
            if profile.display_name and result.get(profile.phone) != "Parents":
                result[profile.phone] = profile.display_name
        for member in db.query(HouseholdMember).filter(HouseholdMember.phone.in_(all_phones)).all():
            if member.display_name and result.get(member.phone) != "Parents":
                result[member.phone] = member.display_name

    for phone in all_phones:
        result.setdefault(phone, phone)
    return result


def _fmt_date(d: date_type | None, date_format: str) -> str:
    formatter = _DATE_FORMATS.get(date_format, _DATE_FORMATS["YYYY-MM-DD"])
    return formatter(d)


def _fmt_currency(amount: float, currency_display: str) -> str:
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if currency_display == "₪":
        return f"{sign}₪{amount:.2f}"
    return f"{sign}{amount:.2f} ILS"


def generate_ledger_xlsx(
    group_jid: str,
    filter_phone: str | None = None,
    fmt_config: dict | None = None,
) -> bytes:
    cfg = fmt_config or {}
    sections: list[str] = cfg.get("sections") or ["balances", "transactions"]
    date_format: str = cfg.get("date_format", "YYYY-MM-DD")
    currency_display: str = cfg.get("currency_display", "ILS")
    include_settled: bool = cfg.get("include_settled", True)
    sort_by: str = cfg.get("sort_by", "date")
    grouping: str = cfg.get("grouping", "none")

    with SessionLocal() as db:
        from app.db.models import HouseholdMember as _HM
        _member = db.query(_HM).filter_by(private_group_jid=group_jid).first()
        _household_id = _member.household_id if _member else None
        q = db.query(LedgerEntry)
        if _household_id:
            q = q.filter(LedgerEntry.household_id == _household_id)
        else:
            q = q.filter(LedgerEntry.group_jid == group_jid)
        if filter_phone:
            q = q.filter(
                or_(LedgerEntry.from_phone == filter_phone, LedgerEntry.to_phone == filter_phone)
            )
        entries = q.order_by(LedgerEntry.transaction_date).all()
        phones = {e.from_phone for e in entries} | {e.to_phone for e in entries}
        names = _phone_to_name_from_db(db, group_jid, phones)

    if not include_settled:
        entries = [e for e in entries if (e.amount_ils - (e.amount_settled_ils or Decimal("0"))) > Decimal("0")]

    if sort_by == "person":
        entries = sorted(entries, key=lambda e: names.get(e.from_phone, e.from_phone))
    elif sort_by == "amount":
        entries = sorted(entries, key=lambda e: e.amount_ils, reverse=True)

    wb = openpyxl.Workbook()
    first_sheet = True

    if "balances" in sections:
        ws = wb.active if first_sheet else wb.create_sheet("Balances")
        ws.title = "Balances"
        first_sheet = False
        _write_balances_sheet(ws, entries, names, date_format, currency_display)

    if "transactions" in sections:
        ws = wb.active if first_sheet else wb.create_sheet("Transactions")
        ws.title = "Transactions"
        first_sheet = False
        _write_transactions_sheet(ws, entries, names, date_format, currency_display, grouping)

    if "settlements" in sections:
        with SessionLocal() as db:
            from app.db.models import LedgerSettlement
            entry_ids = {e.id for e in entries}
            settlements = (
                db.query(LedgerSettlement)
                .filter(
                    or_(
                        LedgerSettlement.payment_leg_id.in_(entry_ids),
                        LedgerSettlement.debt_leg_id.in_(entry_ids),
                    )
                )
                .all()
            )
        ws = wb.active if first_sheet else wb.create_sheet("Settlements")
        ws.title = "Settlements"
        first_sheet = False
        _write_settlements_sheet(ws, settlements, names, date_format, currency_display)

    if first_sheet:
        # No sections matched — write empty sheet to avoid empty workbook error
        wb.active.title = "Empty"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _compute_net_balances(entries: list, names: dict[str, str]) -> dict[tuple[str, str], Decimal]:
    raw: dict[tuple[str, str], Decimal] = {}
    for e in entries:
        remaining = e.amount_ils - (e.amount_settled_ils or Decimal("0"))
        if remaining <= Decimal("0"):
            continue
        frm = names.get(e.from_phone, e.from_phone)
        to = names.get(e.to_phone, e.to_phone)
        if frm == to:
            continue
        key = (frm, to)
        raw[key] = raw.get(key, Decimal("0")) + remaining

    netted: dict[tuple[str, str], Decimal] = {}
    seen: set[tuple[str, str]] = set()
    for (a, b), amt in raw.items():
        canonical = (min(a, b), max(a, b))
        if canonical in seen:
            continue
        seen.add(canonical)
        reverse = raw.get((b, a), Decimal("0"))
        net = amt - reverse
        if net > Decimal("0"):
            netted[(a, b)] = net
        elif net < Decimal("0"):
            netted[(b, a)] = -net
    return netted


def _write_balances_sheet(ws, entries, names, date_format, currency_display) -> None:
    headers = ["Owes", "To", "Amount"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for (frm, to), amt in sorted(_compute_net_balances(entries, names).items()):
        ws.append([frm, to, _fmt_currency(float(amt), currency_display)])


def _write_transactions_sheet(ws, entries, names, date_format, currency_display, grouping) -> None:
    headers = ["Date", "From", "To", "Amount", "Settled", "Remaining", "Description", "Transaction ID"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    last_group_key = None
    for e in entries:
        group_key = None
        if grouping == "month" and e.transaction_date:
            group_key = e.transaction_date.strftime("%Y-%m")
        elif grouping == "person":
            group_key = names.get(e.from_phone, e.from_phone)

        if grouping != "none" and group_key != last_group_key:
            ws.append([f"── {group_key} ──"])
            last_group_key = group_key

        remaining = e.amount_ils - (e.amount_settled_ils or Decimal("0"))
        ws.append([
            _fmt_date(e.transaction_date, date_format),
            names.get(e.from_phone, e.from_phone),
            names.get(e.to_phone, e.to_phone),
            _fmt_currency(float(e.amount_ils), currency_display),
            _fmt_currency(float(e.amount_settled_ils or Decimal("0")), currency_display),
            _fmt_currency(float(remaining), currency_display),
            e.description,
            e.transaction_id,
        ])


def _write_settlements_sheet(ws, settlements, names, date_format, currency_display) -> None:
    headers = ["Payment Leg ID", "Debt Leg ID", "Amount Applied", "Date"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for s in settlements:
        ws.append([
            s.payment_leg_id[:8],
            s.debt_leg_id[:8],
            _fmt_currency(float(s.amount_ils), currency_display),
            _fmt_date(s.created_at.date() if s.created_at else None, date_format),
        ])


def generate_ledger_pdf(
    group_jid: str,
    filter_phone: str | None = None,
    fmt_config: dict | None = None,
) -> bytes:
    """Generate a PDF ledger report. Supports Hebrew/RTL via pdf_report helpers.

    Layout:
      - Net balances summary (unchanged).
      - One ledger table per counterparty pair: date | description | {name A} |
        {name B} | comments. Each row's amount is placed under whichever side
        is the from_phone (the one who owed or paid), with a totals row summing
        each column so the two sums reproduce the net-balance figure above.

    fmt_config keys (from ReportFormat, same as generate_ledger_xlsx):
      date_format, currency_display, include_settled, sort_by.
    """
    import io as _io
    from datetime import datetime, timezone

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    # Import RTL/Hebrew utilities from pdf_report
    from app.reports.pdf_report import _bidi, _font, _register_font, _xml

    cfg = fmt_config or {}
    date_format: str = cfg.get("date_format", "YYYY-MM-DD")
    currency_display: str = cfg.get("currency_display", "ILS")
    include_settled: bool = cfg.get("include_settled", True)
    sort_by: str = cfg.get("sort_by", "date")

    # Language: explicit fmt_config (call-time override or saved ReportFormat)
    # takes priority; else fall back to GroupConfig.feedback_language.
    lang = cfg.get("language")
    if not lang:
        lang = "en"
        try:
            with SessionLocal() as _db:
                from app.db.models import GroupConfig
                gcfg = _db.get(GroupConfig, group_jid)
                if gcfg and gcfg.feedback_language:
                    lang = gcfg.feedback_language
        except Exception:
            pass

    rtl = (lang == "he")
    _register_font()

    LABELS = {
        "en": {
            "title": "Family Ledger",
            "generated": "Generated",
            "net_balances": "Net Balances",
            "transactions": "Transactions",
            "from": "From",
            "to": "To",
            "amount": "Amount (₪)",
            "date": "Date",
            "description": "Description",
            "comments": "Comments",
            "settled": "Settled",
            "payment": "Payment",
            "remaining": "remaining",
            "total": "Total",
            "all_settled": "All debts settled.",
            "no_transactions": "No transactions found.",
        },
        "he": {
            "title": "ספר חשבונות משפחתי",
            "generated": "הופק",
            "net_balances": "יתרות נטו",
            "transactions": "עסקאות",
            "from": "מ",
            "to": "ל",
            "amount": "סכום (₪)",
            "date": "תאריך",
            "description": "תיאור",
            "comments": "הערות",
            "settled": "שולם",
            "payment": "תשלום",
            "remaining": "נותר",
            "total": "סה״כ",
            "all_settled": "כל החובות סולקו.",
            "no_transactions": "לא נמצאו עסקאות.",
        },
    }
    L = LABELS.get(lang, LABELS["en"])

    def _t(text: str) -> str:
        # Bidi is applied unconditionally, not gated on the report's overall
        # language: a Hebrew description/name can appear inside an English
        # report (or vice versa) since users write free text in either script.
        # get_display() is a no-op for pure-LTR text, so this is always safe.
        # Bidi reorder the raw text first, THEN XML-escape — reversing this
        # order corrupts any text containing a literal ", &, <, or > because
        # bidi would reorder the escaped entity's characters individually.
        return _xml(_bidi(text))

    font_n = _font(lang, bold=False)
    font_b = _font(lang, bold=True)

    from reportlab.lib.enums import TA_RIGHT, TA_LEFT
    align_enum = TA_RIGHT if rtl else TA_LEFT

    styles = getSampleStyleSheet()
    normal_style = ParagraphStyle("LN", fontName=font_n, fontSize=9, leading=11, alignment=align_enum)
    bold_style   = ParagraphStyle("LB", fontName=font_b, fontSize=9, leading=11, alignment=align_enum)
    title_style  = ParagraphStyle("LT", fontName=font_b, fontSize=14, leading=18,
                                  alignment=2 if rtl else 0, textColor=colors.HexColor("#1a3c5e"))
    meta_style   = ParagraphStyle("LM", fontName=font_n, fontSize=8, leading=10,
                                  alignment=2 if rtl else 0, textColor=colors.HexColor("#555555"))
    h2_style     = ParagraphStyle("LH2", fontName=font_b, fontSize=11, leading=14,
                                  alignment=2 if rtl else 0, spaceBefore=8, spaceAfter=4)
    h3_style     = ParagraphStyle("LH3", fontName=font_b, fontSize=9.5, leading=12,
                                  alignment=2 if rtl else 0, spaceBefore=6, spaceAfter=2,
                                  textColor=colors.HexColor("#1a3c5e"))

    def _p(text: str, style=None) -> Paragraph:
        return Paragraph(_t(text), style or normal_style)

    def _pb(text: str) -> Paragraph:
        return Paragraph(_t(text), bold_style)

    with SessionLocal() as db:
        from app.db.models import LedgerEntry as _LE, HouseholdMember as _HM2
        _member2 = db.query(_HM2).filter_by(private_group_jid=group_jid).first()
        _household_id2 = _member2.household_id if _member2 else None
        query = db.query(_LE)
        if _household_id2:
            query = query.filter(_LE.household_id == _household_id2)
        else:
            query = query.filter(_LE.group_jid == group_jid)
        if filter_phone:
            from sqlalchemy import or_
            query = query.filter(or_(
                _LE.from_phone == filter_phone,
                _LE.to_phone == filter_phone,
            ))
        entries = query.order_by(_LE.transaction_date).all()

        phones = {e.from_phone for e in entries} | {e.to_phone for e in entries}
        names = _phone_to_name_from_db(db, group_jid, phones)

    buf = _io.BytesIO()
    MARGIN = 2.0 * cm
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=MARGIN, bottomMargin=MARGIN)
    story = []

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    story.append(Paragraph(_t(L["title"]), title_style))
    story.append(Paragraph(_t(f"{L['generated']}: {generated}"), meta_style))
    story.append(Spacer(1, 0.5 * cm))

    # ── Net balances: one netted line per pair (who owes whom, net amount) ────
    story.append(Paragraph(_t(L["net_balances"]), h2_style))

    # Reuses the same netting logic as the XLSX balances sheet: opposing
    # debts between the same two people are offset against each other so
    # each pair produces exactly one directional line, not two raw gross ones.
    net = _compute_net_balances(entries, names)

    HDR_BG   = colors.HexColor("#1a3c5e")
    ALT_BG   = colors.HexColor("#f0f4f8")
    TOTAL_BG = colors.HexColor("#e8f0fe")

    if net:
        if rtl:
            hdrs = [_pb(L["amount"]), _pb(L["to"]), _pb(L["from"])]
        else:
            hdrs = [_pb(L["from"]), _pb(L["to"]), _pb(L["amount"])]
        bal_rows = [hdrs]
        for (frm, to), amt in sorted(net.items()):
            frm_name = _p(frm)
            to_name  = _p(to)
            amt_p    = Paragraph(
                _fmt_currency(float(amt), currency_display),
                ParagraphStyle("R", fontName=font_n, fontSize=9, alignment=2),
            )
            if rtl:
                bal_rows.append([amt_p, to_name, frm_name])
            else:
                bal_rows.append([frm_name, to_name, amt_p])
        avail = A4[0] - 2 * MARGIN
        col_w = [avail * 0.35, avail * 0.35, avail * 0.30]
        if rtl:
            col_w = list(reversed(col_w))
        bal_table = Table(bal_rows, colWidths=col_w)
        bal_table.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0),  HDR_BG),
            ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, ALT_BG]),
            ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#c0ccd8")),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 5),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ]))
        story.append(bal_table)
    else:
        story.append(Paragraph(_t(L["all_settled"]), normal_style))

    story.append(Spacer(1, 0.5 * cm))

    # ── Ledger: one date/description/side-A/side-B/comments table per pair ────
    story.append(Paragraph(_t(L["transactions"]), h2_style))

    ledger_entries = entries
    if not include_settled:
        ledger_entries = [
            e for e in entries
            if e.entry_type == "payment"
            or (e.amount_ils - (e.amount_settled_ils or Decimal("0"))) > 0
        ]

    pairs: dict[tuple[str, str], list] = {}
    for e in ledger_entries:
        key = tuple(sorted((e.from_phone, e.to_phone)))
        pairs.setdefault(key, []).append(e)

    if not pairs:
        story.append(Paragraph(_t(L["no_transactions"]), normal_style))
    else:
        avail = A4[0] - 2 * MARGIN
        col_w = [avail * 0.11, avail * 0.29, avail * 0.16, avail * 0.16, avail * 0.28]
        if rtl:
            col_w = list(reversed(col_w))

        right_style      = ParagraphStyle("PR",  fontName=font_n, fontSize=7, alignment=2)
        right_bold_style = ParagraphStyle("PRB", fontName=font_b, fontSize=7, alignment=2)
        cell_style       = ParagraphStyle("PC",  fontName=font_n, fontSize=7, alignment=2 if rtl else 0)

        for (phone_a, phone_b), rows in sorted(
            pairs.items(),
            key=lambda kv: (names.get(kv[0][0], kv[0][0]), names.get(kv[0][1], kv[0][1])),
        ):
            name_a = names.get(phone_a, phone_a)
            name_b = names.get(phone_b, phone_b)

            story.append(Paragraph(_t(f"{name_a} — {name_b}"), h3_style))

            if sort_by == "amount":
                rows_sorted = sorted(rows, key=lambda e: e.amount_ils, reverse=True)
            else:
                rows_sorted = sorted(rows, key=lambda e: (e.transaction_date, e.created_at or e.transaction_date))

            headers = [L["date"], L["description"], name_a, name_b, L["comments"]]
            hdr_row = [_pb(h) for h in (reversed(headers) if rtl else headers)]
            table_rows = [hdr_row]

            sum_a = Decimal("0")
            sum_b = Decimal("0")
            for e in rows_sorted:
                date_s = _fmt_date(e.transaction_date, date_format)
                desc_s = (e.description or "")[:60]
                remaining = e.amount_ils - (e.amount_settled_ils or Decimal("0"))

                # Payments reduce what the payer owes, so they're signed negative;
                # debts increase it, so they stay positive. The column totals
                # below are the signed sum, which is what reproduces the net
                # balance figure shown in the summary above.
                signed_amount = -e.amount_ils if e.entry_type == "payment" else e.amount_ils
                amt_s = _fmt_currency(float(signed_amount), currency_display)

                if e.entry_type == "payment":
                    comment_s = L["payment"]
                elif remaining <= Decimal("0"):
                    comment_s = L["settled"]
                else:
                    comment_s = f"{_fmt_currency(float(remaining), currency_display)} {L['remaining']}"

                if e.from_phone == phone_a:
                    col_a, col_b = amt_s, ""
                    sum_a += signed_amount
                else:
                    col_a, col_b = "", amt_s
                    sum_b += signed_amount

                cells = [
                    Paragraph(date_s, cell_style),
                    Paragraph(_t(desc_s), cell_style),
                    Paragraph(col_a, right_style),
                    Paragraph(col_b, right_style),
                    Paragraph(_t(comment_s), cell_style),
                ]
                if rtl:
                    cells = list(reversed(cells))
                table_rows.append(cells)

            total_cells = [
                Paragraph("", cell_style),
                _pb(L["total"]),
                Paragraph(_fmt_currency(float(sum_a), currency_display), right_bold_style),
                Paragraph(_fmt_currency(float(sum_b), currency_display), right_bold_style),
                Paragraph("", cell_style),
            ]
            if rtl:
                total_cells = list(reversed(total_cells))
            table_rows.append(total_cells)

            t = Table(table_rows, colWidths=col_w, repeatRows=1)
            t.setStyle(TableStyle([
                ("BACKGROUND",     (0, 0), (-1, 0),  HDR_BG),
                ("TEXTCOLOR",      (0, 0), (-1, 0),  colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, ALT_BG]),
                ("BACKGROUND",     (0, -1), (-1, -1), TOTAL_BG),
                ("GRID",           (0, 0), (-1, -1), 0.4, colors.HexColor("#c0ccd8")),
                ("TOPPADDING",     (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING",  (0, 0), (-1, -1), 3),
                ("LEFTPADDING",    (0, 0), (-1, -1), 4),
                ("RIGHTPADDING",   (0, 0), (-1, -1), 4),
                ("VALIGN",         (0, 0), (-1, -1), "TOP"),
            ]))
            story.append(t)
            story.append(Spacer(1, 0.4 * cm))

    doc.build(story)
    return buf.getvalue()
