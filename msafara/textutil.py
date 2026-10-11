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
