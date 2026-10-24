"""The plain-text invoice the customer receives."""

from .discounts import line_net
from .money import apply_rate, format_amount
from .totals import invoice_totals


def line_gross(line):
    """What the customer sees on a line: its net price with VAT."""
    return apply_rate(line_net(line), 10000 + line.tax_rate_bp)


def render_text(invoice, customer):
    rows = [f"Invoice {invoice.number or '(draft)'}", f"Bill to: {customer.name}", ""]
    if customer.vat_number:
        rows.insert(2, f"VAT number: {customer.vat_number}")
    for line in invoice.lines:
        label = f"{line.quantity} x {line.description}"
        if line.discount_bp:
            label += f" (-{line.discount_bp / 100:g}%)"
        rows.append(f"{label:<40} {format_amount(line_gross(line), invoice.currency):>14}")
    totals = invoice_totals(invoice)
    rows += ["",
             f"{'Net':<40} {format_amount(totals['net'], invoice.currency):>14}",
             f"{'VAT':<40} {format_amount(totals['tax'], invoice.currency):>14}",
             f"{'Total':<40} {format_amount(totals['gross'], invoice.currency):>14}"]
    if invoice.due_on:
        rows.append(f"Due on {invoice.due_on}")
    return "\n".join(rows)
