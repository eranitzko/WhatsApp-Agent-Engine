"""Hebrew / English labels for reports."""

from __future__ import annotations

LABELS: dict[str, dict[str, str]] = {
    "en": {
        "report_title_default": "Monthly Invoice Report",
        "prepared_by": "Prepared by",
        "period": "Period",
        "generated": "Generated",
        "no_invoices": "No invoices found for this period.",
        "col_date": "Date",
        "col_invoice_no": "Invoice #",
        "col_vendor": "Vendor",
        "col_description": "Description",
        "col_amount": "Amount",
        "col_amount_orig": "Orig. Amount",
        "col_amount_ils": "Amount (ILS)",
        "col_flagged": "Flagged",
        "total": "Total",
        "total_ils": "Total (ILS)",
        "flagged_note": "* Flagged for review",
        "appendix_title": "Invoice Images",
        "appendix_label": "Invoice #{number} — {vendor}",
        "col_currency": "Currency",
        "col_flag_reason": "Flag Reason",
        "flagged_yes": "Yes",
        "flagged_no": "",
    },
    "he": {
        "report_title_default": "דוח חשבוניות חודשי",
        "prepared_by": "הוכן על ידי",
        "period": "תקופה",
        "generated": "נוצר",
        "no_invoices": "לא נמצאו חשבוניות לתקופה זו.",
        "col_date": "תאריך",
        "col_invoice_no": "מס׳ חשבונית",
        "col_vendor": "ספק",
        "col_description": "תיאור",
        "col_amount": "סכום",
        "col_amount_orig": "סכום מקורי",
        "col_amount_ils": "סכום (₪)",
        "col_flagged": "סומן",
        "total": "סה״כ",
        "total_ils": "סה״כ (₪)",
        "flagged_note": "* סומן לבדיקה",
        "appendix_title": "תמונות חשבוניות",
        "appendix_label": "חשבונית #{number} — {vendor}",
        "col_currency": "מטבע",
        "col_flag_reason": "סיבת סימון",
        "flagged_yes": "כן",
        "flagged_no": "",
    },
}


def get(lang: str, key: str, **kwargs) -> str:
    lang = lang if lang in LABELS else "en"
    text = LABELS[lang].get(key, LABELS["en"].get(key, key))
    return text.format(**kwargs) if kwargs else text
