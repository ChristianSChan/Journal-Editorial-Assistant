"""Deterministic statistical consistency checks for manuscript text.

This is a lightweight, statcheck-inspired pass for APA-style NHST reports.
It checks whether reported p-values are broadly consistent with reported
t, F, chi-square, z, and r tests. It is not a substitute for a statistical
review, but it gives the editor concrete places to inspect.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


MAX_CHECKS = 80
EPS = 1e-12


@dataclass(frozen=True)
class StatisticalCheck:
    test_type: str
    statistic: float
    dfs: tuple[float, ...]
    reported_p: str
    computed_p: float | None
    status: str
    message: str
    snippet: str


def run_statistical_checks(text: str) -> list[StatisticalCheck]:
    """Return statcheck-like consistency findings from manuscript text."""
    if not text.strip():
        return []

    normalized = _normalize_text(text)
    checks: list[StatisticalCheck] = []
    for match in _iter_test_matches(normalized):
        if len(checks) >= MAX_CHECKS:
            break
        check = _build_check(match)
        if check:
            checks.append(check)
    return checks


def summarize_statistical_checks(checks: list[StatisticalCheck]) -> list[str]:
    """Convert checks into editor-facing flags."""
    if not checks:
        return ["No APA-style test statistic/p-value pairs were detected for deterministic checking."]

    inconsistent = [item for item in checks if item.status == "inconsistent"]
    warning = [item for item in checks if item.status == "warning"]
    consistent = [item for item in checks if item.status == "consistent"]

    flags = [
        (
            f"Automated p-value consistency check scanned {len(checks)} APA-style test report(s): "
            f"{len(inconsistent)} potential inconsistency, {len(warning)} borderline warning, "
            f"{len(consistent)} broadly consistent."
        )
    ]
    for item in inconsistent[:8]:
        flags.append(item.message)
    for item in warning[:4]:
        flags.append(item.message)
    if len(inconsistent) > 8:
        flags.append(f"{len(inconsistent) - 8} additional potential p-value inconsistencies were found.")
    return flags


def checks_to_dicts(checks: list[StatisticalCheck]) -> list[dict[str, object]]:
    return [
        {
            "test_type": item.test_type,
            "statistic": item.statistic,
            "dfs": list(item.dfs),
            "reported_p": item.reported_p,
            "computed_p": item.computed_p,
            "status": item.status,
            "message": item.message,
            "snippet": item.snippet,
        }
        for item in checks
    ]


def _normalize_text(text: str) -> str:
    text = text.replace("\u2212", "-")
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = text.replace("\u03c7\u00b2", "chi2").replace("\u03c7 2", "chi2")
    text = text.replace("\u03c7", "chi")
    text = text.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text)


def _iter_test_matches(text: str):
    specs = [
        (
            "t",
            re.compile(
                r"\bt\s*\(\s*(?P<df>\d+(?:\.\d+)?)\s*\)\s*=\s*(?P<stat>-?\d+(?:\.\d+)?)",
                re.IGNORECASE,
            ),
        ),
        (
            "F",
            re.compile(
                r"\bF\s*\(\s*(?P<df1>\d+(?:\.\d+)?)\s*,\s*(?P<df2>\d+(?:\.\d+)?)\s*\)\s*=\s*(?P<stat>\d+(?:\.\d+)?)",
                re.IGNORECASE,
            ),
        ),
        (
            "chi-square",
            re.compile(
                r"\b(?:chi2|chi-square|chi\s*square|x2|X2)\s*\(\s*(?P<df>\d+(?:\.\d+)?)\s*\)\s*=\s*(?P<stat>\d+(?:\.\d+)?)",
                re.IGNORECASE,
            ),
        ),
        (
            "z",
            re.compile(r"\bz\s*=\s*(?P<stat>-?\d+(?:\.\d+)?)", re.IGNORECASE),
        ),
        (
            "r",
            re.compile(
                r"\br\s*\(\s*(?P<df>\d+(?:\.\d+)?)\s*\)\s*=\s*(?P<stat>-?\d+(?:\.\d+)?)",
                re.IGNORECASE,
            ),
        ),
    ]
    found = []
    for test_type, pattern in specs:
        for match in pattern.finditer(text):
            p_report = _find_p_report(text[match.end() : match.end() + 160])
            if p_report:
                found.append((match.start(), match.end(), test_type, match, p_report))
    yield from sorted(found, key=lambda item: item[0])


def _find_p_report(window: str) -> tuple[str, str, float, int] | None:
    match = re.search(
        r"\bp\s*(?P<op>=|<|>|<=|>=)\s*(?P<value>\.?\d+(?:\.\d+)?)",
        window,
        re.IGNORECASE,
    )
    if not match:
        return None
    raw_value = match.group("value")
    if raw_value.startswith("."):
        raw_value = f"0{raw_value}"
    value = float(raw_value)
    decimals = len(raw_value.split(".")[1]) if "." in raw_value else 0
    return match.group("op"), match.group("value"), value, decimals


def _build_check(match_data) -> StatisticalCheck | None:
    start, end, test_type, match, p_report = match_data
    operator, raw_p, reported_value, decimals = p_report
    try:
        statistic = float(match.group("stat"))
        dfs = _extract_dfs(test_type, match)
        computed = _computed_p(test_type, statistic, dfs)
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    if computed is None or not math.isfinite(computed):
        return None

    status, message = _compare_p(
        test_type=test_type,
        statistic=statistic,
        dfs=dfs,
        operator=operator,
        reported_value=reported_value,
        raw_p=raw_p,
        decimals=decimals,
        computed=computed,
    )
    source = match.string
    snippet = source[max(0, start - 70) : min(len(source), end + 170)].strip()
    return StatisticalCheck(
        test_type=test_type,
        statistic=statistic,
        dfs=dfs,
        reported_p=f"p {operator} {raw_p}",
        computed_p=computed,
        status=status,
        message=message,
        snippet=snippet,
    )


def _extract_dfs(test_type: str, match) -> tuple[float, ...]:
    if test_type == "F":
        return (float(match.group("df1")), float(match.group("df2")))
    if "df" in match.groupdict():
        return (float(match.group("df")),)
    return ()


def _computed_p(test_type: str, statistic: float, dfs: tuple[float, ...]) -> float | None:
    if test_type == "z":
        return math.erfc(abs(statistic) / math.sqrt(2))
    if test_type == "t" and dfs:
        return _t_two_tailed_p(abs(statistic), dfs[0])
    if test_type == "F" and len(dfs) == 2:
        return _f_survival_p(statistic, dfs[0], dfs[1])
    if test_type == "chi-square" and dfs:
        return _chi_square_survival_p(statistic, dfs[0])
    if test_type == "r" and dfs and abs(statistic) < 1:
        t_value = abs(statistic) * math.sqrt(dfs[0] / max(EPS, 1 - statistic * statistic))
        return _t_two_tailed_p(t_value, dfs[0])
    return None


def _compare_p(
    test_type: str,
    statistic: float,
    dfs: tuple[float, ...],
    operator: str,
    reported_value: float,
    raw_p: str,
    decimals: int,
    computed: float,
) -> tuple[str, str]:
    tolerance = max(0.0005, 0.5 * (10 ** -max(decimals, 3)))
    reported_sig = _reported_significant(operator, reported_value)
    computed_sig = computed < 0.05
    label = _format_test_label(test_type, statistic, dfs)
    computed_text = _format_p(computed)

    if operator == "=":
        if abs(computed - reported_value) <= tolerance:
            return "consistent", f"{label}: reported p = {raw_p}; computed p is approximately {computed_text}."
        if reported_sig != computed_sig:
            return (
                "inconsistent",
                f"{label}: reported p = {raw_p}, but the recomputed p is approximately {computed_text}; this may change the significance decision.",
            )
        return (
            "warning",
            f"{label}: reported p = {raw_p}, while the recomputed p is approximately {computed_text}; inspect rounding/reporting.",
        )

    if operator in {"<", "<="}:
        if computed <= reported_value + tolerance:
            return "consistent", f"{label}: reported p {operator} {raw_p}; computed p is approximately {computed_text}."
        return (
            "inconsistent" if reported_sig != computed_sig else "warning",
            f"{label}: reported p {operator} {raw_p}, but the recomputed p is approximately {computed_text}.",
        )

    if operator in {">", ">="}:
        if computed >= reported_value - tolerance:
            return "consistent", f"{label}: reported p {operator} {raw_p}; computed p is approximately {computed_text}."
        return (
            "inconsistent" if reported_sig != computed_sig else "warning",
            f"{label}: reported p {operator} {raw_p}, but the recomputed p is approximately {computed_text}.",
        )

    return "warning", f"{label}: could not compare reported p {operator} {raw_p} with computed p {computed_text}."


def _reported_significant(operator: str, value: float) -> bool:
    if operator in {"<", "<="}:
        return value <= 0.05
    if operator in {">", ">="}:
        return False if value >= 0.05 else True
    return value < 0.05


def _format_test_label(test_type: str, statistic: float, dfs: tuple[float, ...]) -> str:
    if len(dfs) == 2:
        return f"{test_type}({dfs[0]:g}, {dfs[1]:g}) = {statistic:g}"
    if len(dfs) == 1:
        return f"{test_type}({dfs[0]:g}) = {statistic:g}"
    return f"{test_type} = {statistic:g}"


def _format_p(value: float) -> str:
    if value < 0.001:
        return "< .001"
    return f"{value:.3f}".replace("0.", ".")


def _t_two_tailed_p(t_value: float, df: float) -> float:
    x = df / (df + t_value * t_value)
    return _betai(df / 2, 0.5, x)


def _f_survival_p(f_value: float, df1: float, df2: float) -> float:
    if f_value < 0:
        return 1.0
    x = df2 / (df2 + df1 * f_value)
    return _betai(df2 / 2, df1 / 2, x)


def _chi_square_survival_p(value: float, df: float) -> float:
    return _gammaincc(df / 2, value / 2)


def _betai(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return bt * _betacf(a, b, x) / a
    return 1 - bt * _betacf(b, a, 1 - x) / b


def _betacf(a: float, b: float, x: float) -> float:
    qab = a + b
    qap = a + 1
    qam = a - 1
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < EPS:
        d = EPS
    d = 1.0 / d
    h = d
    for m in range(1, 120):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < EPS:
            d = EPS
        c = 1.0 + aa / c
        if abs(c) < EPS:
            c = EPS
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < EPS:
            d = EPS
        c = 1.0 + aa / c
        if abs(c) < EPS:
            c = EPS
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-7:
            break
    return h


def _gammaincc(a: float, x: float) -> float:
    if x <= 0:
        return 1.0
    if x < a + 1:
        return 1 - _gser(a, x)
    return _gcf(a, x)


def _gser(a: float, x: float) -> float:
    ap = a
    summation = 1.0 / a
    delta = summation
    for _ in range(120):
        ap += 1
        delta *= x / ap
        summation += delta
        if abs(delta) < abs(summation) * 3e-7:
            break
    return summation * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gcf(a: float, x: float) -> float:
    b = x + 1 - a
    c = 1.0 / EPS
    d = 1.0 / b if abs(b) > EPS else 1.0 / EPS
    h = d
    for i in range(1, 120):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        if abs(d) < EPS:
            d = EPS
        c = b + an / c
        if abs(c) < EPS:
            c = EPS
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-7:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h
