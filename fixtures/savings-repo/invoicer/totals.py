from .discounts import line_net
from .tax import line_tax


def line_totals(line):
    net, tax = line_net(line), line_tax(line)
    return {"net": net, "tax": tax, "gross": net + tax}


def invoice_totals(invoice):
    net = sum(line_net(line) for line in invoice.lines)
    tax = sum(line_tax(line) for line in invoice.lines)
    return {"net": net, "tax": tax, "gross": net + tax}
