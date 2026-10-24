"""VAT on one line (docs/adr/0003)."""

from .discounts import line_net
from .errors import InvalidAmount

STANDARD_RATES_BP = {"IE": 2300, "ES": 2100, "FR": 2000, "DE": 1900, "PT": 2300, "GB": 2000}
REDUCED_RATES_BP = {"IE": 1350, "ES": 1000, "FR": 550, "DE": 700, "PT": 1300, "GB": 500}


def rate_for(country, reduced=False):
    table = REDUCED_RATES_BP if reduced else STANDARD_RATES_BP
    try:
        return table[country]
    except KeyError:
        raise InvalidAmount(f"no VAT rate for {country}") from None


def line_tax(line):
    return line_net(line) * line.tax_rate_bp // 10000
