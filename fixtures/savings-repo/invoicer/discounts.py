from .errors import InvalidAmount
from .money import apply_rate


def line_discount(line):
    """A line's discount in cents: basis points of quantity × unit price, rounded half up."""
    if not 0 <= line.discount_bp <= 10000:
        raise InvalidAmount(f"discount out of range: {line.discount_bp}")
    return apply_rate(line.quantity * line.unit_cents, line.discount_bp)


def line_net(line):
    return line.quantity * line.unit_cents - line_discount(line)
