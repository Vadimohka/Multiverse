import re
from decimal import Decimal
from typing import Any


def normalize_number(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    text = str(value).strip().lower().replace("\u00a0", " ")
    text = text.replace("процента", "").replace("процентов", "").replace("годовых", "").replace("%", "")
    match = re.search(r"[-+]?\d+(?:[\s.,]\d+)*", text)
    if not match:
        return None
    number = match.group(0).replace(" ", "")
    if "," in number and "." in number:
        if number.rfind(",") > number.rfind("."):
            number = number.replace(".", "").replace(",", ".")
        else:
            number = number.replace(",", "")
    else:
        number = number.replace(",", ".")
    return Decimal(number)


def normalize_term(value: str, days_per_month: int = 30, days_per_year: int = 365) -> dict[str, Any]:
    text = value.strip().lower().replace("–", "-").replace("—", "-")
    nums = [int(x) for x in re.findall(r"\d+", text)]
    multiplier = days_per_year if "год" in text or "лет" in text else days_per_month if "мес" in text else 1
    result: dict[str, Any] = {"term_original": value, "term_min_days": None, "term_max_days": None}
    if not nums:
        return result
    vals = [n * multiplier for n in nums]
    if "до " in text:
        result["term_min_days"] = 0
        result["term_max_days"] = vals[0]
    elif "свыше" in text or "от " in text:
        result["term_min_days"] = vals[0]
    elif len(vals) >= 2:
        result["term_min_days"], result["term_max_days"] = vals[0], vals[1]
    else:
        result["term_min_days"] = result["term_max_days"] = vals[0]
    return result


def normalize_currency(value: str) -> str | None:
    text = value.upper().strip()
    aliases = {"BR": "BYN", "БЕЛ. РУБ.": "BYN", "BYR": "BYN", "$": "USD", "€": "EUR", "₽": "RUB"}
    if text in {"BYN", "USD", "EUR", "RUB", "CNY"}:
        return text
    return aliases.get(text)


def parse_rate_expression(value: str) -> dict[str, Any]:
    text = value.strip()
    lower = text.lower()
    if "индивидуал" in lower or "соглашению" in lower:
        return {"rate_type": "INDIVIDUAL", "rate_value": None, "rate_formula": text}
    if "ср" in lower or "рефинанс" in lower:
        spread = normalize_number(text.split("+")[-1] if "+" in text else text.split("минус")[-1])
        if "минус" in lower and spread is not None:
            spread = -spread
        return {"rate_type": "BENCHMARK_SPREAD", "rate_value": None, "rate_formula": text, "spread_pp": float(spread) if spread is not None else None}
    number = normalize_number(text)
    return {"rate_type": "FIXED" if number is not None else "NOT_PUBLISHED", "rate_value": float(number) if number is not None else None, "rate_formula": None if number is not None else text}
