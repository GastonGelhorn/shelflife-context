"""Amounts are integers in cents. Rates are integers in basis points (2100 = 21%)."""

CURRENCY_SYMBOLS = {"EUR": "€", "GBP": "£", "USD": "$"}


def round_half_up(numerator, denominator):
    """numerator / denominator rounded to the nearest integer, halves away from zero."""
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    sign = -1 if numerator < 0 else 1
    quotient, remainder = divmod(abs(numerator), denominator)
    if remainder * 2 >= denominator:
        quotient += 1
    return sign * quotient


def apply_rate(cents, basis_points):
    """cents × rate, rounded half up."""
    return round_half_up(cents * basis_points, 10000)


def format_amount(cents, currency="EUR"):
    """For people: '€1,234.50', '-€3.00'."""
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(cents), 100)
    return f"{sign}{CURRENCY_SYMBOLS.get(currency, currency + ' ')}{whole:,}.{part:02d}"


def decimal_string(cents):
    """For files: '1234.50', '-3.00'."""
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(cents), 100)
    return f"{sign}{whole}.{part:02d}"


def parse_amount(text):
    """'12.50' or '12,50' -> 1250."""
    cleaned = text.strip().replace(",", ".")
    if not cleaned or cleaned.count(".") > 1:
        raise ValueError(f"not an amount: {text!r}")
    whole, _, part = cleaned.partition(".")
    if len(part) > 2:
        raise ValueError(f"more than two decimals: {text!r}")
    sign = -1 if whole.startswith("-") else 1
    return sign * (abs(int(whole or "0")) * 100 + int((part + "00")[:2]))
