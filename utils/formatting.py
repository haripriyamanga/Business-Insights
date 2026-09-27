"""Small helpers that turn numbers into readable text."""

from __future__ import annotations

import math


def format_currency(value: float, compact: bool = False) -> str:
    """Format a money value, e.g. 1234.5 -> '$1,234.50' or (compact) '$1.2K'."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    if compact:
        sign = "-" if value < 0 else ""
        amount = abs(value)
        for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
            if amount >= threshold:
                return f"{sign}${amount / threshold:,.1f}{suffix}"
        return f"{sign}${amount:,.0f}"
    return f"-${abs(value):,.2f}" if value < 0 else f"${value:,.2f}"


def format_number(value: float) -> str:
    """Format a count with thousands separators; keeps decimals only when needed."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def format_percent(value: float, signed: bool = False) -> str:
    """Format a ratio as a percentage, e.g. 0.253 -> '25.3%' (or '+25.3%')."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    return f"{value:+.1%}" if signed else f"{value:.1%}"
