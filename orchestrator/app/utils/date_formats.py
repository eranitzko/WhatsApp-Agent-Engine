"""Utilities for parsing admin-defined date format strings.

Format strings use D (day), M (month), Y (year) and one separator (/, -, ., space).
Example: "MM/DD/YYYY", "DD.MM.YY", "YYYY-MM-DD"
"""
from __future__ import annotations

import re
from datetime import date


def parse_format_string(fmt: str) -> dict | None:
    """Parse an admin-supplied format string into a format dict.

    Returns {"order": ["d","m","y" in position order], "sep": char, "normalized": str}
    or None if the string is not a valid format.
    """
    fmt = fmt.upper().strip()

    sep = None
    for candidate in ['/', '-', '.', ' ']:
        if candidate in fmt:
            sep = candidate
            break
    if sep is None:
        return None

    parts = fmt.split(sep)
    if len(parts) != 3:
        return None

    order = []
    for part in parts:
        if 'D' in part:
            order.append('d')
        elif 'M' in part:
            order.append('m')
        elif 'Y' in part:
            order.append('y')
        else:
            return None

    if set(order) != {'d', 'm', 'y'}:
        return None

    return {"order": order, "sep": sep, "normalized": fmt}


def try_fmt(raw_date: str, fmt: dict) -> date | None:
    """Attempt to parse raw_date using a format dict from parse_format_string.

    Returns a date object on success, None if no match or invalid date.
    """
    sep = re.escape(fmt['sep'])
    widths = {'d': r'\d{1,2}', 'm': r'\d{1,2}', 'y': r'\d{2,4}'}
    pattern = sep.join(f"({widths[c]})" for c in fmt['order'])
    m = re.search(pattern, raw_date)
    if not m:
        return None
    vals = {fmt['order'][i]: int(m.group(i + 1)) for i in range(3)}
    y = vals['y']
    if y < 100:
        y += 2000
    try:
        return date(y, vals['m'], vals['d'])
    except ValueError:
        return None
