"""Small text helpers shared by agents, guardrails and metrics."""
import re
from statistics import median

_NUM = r"(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_CUR = r"(?:INR|USD|Rs\.?|₹|\$)"
_CUR_BEFORE = re.compile(rf"{_CUR}\s*{_NUM}", re.I)
_CUR_AFTER = re.compile(rf"{_NUM}\s*{_CUR}(?![A-Za-z])", re.I)


def to_float(s: str) -> float:
    return float(s.replace(",", ""))


def currency_numbers(text: str) -> list[float]:
    """Every number written directly next to a currency marker (INR 7,100 / $135 / 7100 INR)."""
    out = [to_float(m.group(1)) for m in _CUR_BEFORE.finditer(text)]
    out += [to_float(m.group(1)) for m in _CUR_AFTER.finditer(text)]
    return out


def fmt_money(amount: float, cur: str) -> str:
    return f"{cur} {amount:,.0f}"


def ceil_money(x: float) -> int:
    import math
    return int(math.ceil(round(x, 6)))


def med(values):
    return float(median(values)) if values else None


_K = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*k\b", re.I)
_PER_UNIT = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{2,3})+|\d+(?:\.\d+)?)\s*(?:/-\s*)?(?:per|a|each|/)\s*"
                       r"(?:room|night|person|guest|head|pax|vehicle|car|day|seat)\b", re.I)
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b|per cent\b)", re.I)
_WORD_PCT = re.compile(r"\b(?:five|ten|fifteen|twenty)\s+(?:percent|per cent)\b", re.I)


def money_mentions(text: str) -> list[float]:
    """Amounts that read as money even without a currency marker: currency-marked numbers, '6k', and
    '6,000 per room' style rates (40 or more, so '2 per room' occupancy is not mistaken for a price)."""
    out = currency_numbers(text)
    out += [to_float(m.group(1)) * 1000 for m in _K.finditer(text)]
    out += [v for v in (to_float(m.group(1)) for m in _PER_UNIT.finditer(text)) if v >= 40]
    return out


def percentages(text: str) -> list[str]:
    return [m.group(0) for m in _PCT.finditer(text)] + [m.group(0) for m in _WORD_PCT.finditer(text)]
