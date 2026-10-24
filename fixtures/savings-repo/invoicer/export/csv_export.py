"""The CSV the accountants download: one row per numbered invoice."""

import csv
import io

from ..money import decimal_string
from ..totals import invoice_totals

HEADER = ["number", "customer_id", "issued_on", "status", "net", "tax", "gross", "currency"]


def to_csv(invoices):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(HEADER)
    for invoice in sorted((i for i in invoices if i.number), key=lambda i: i.number):
        totals = invoice_totals(invoice)
        writer.writerow([invoice.number, invoice.customer_id, invoice.issued_on, invoice.status,
                         decimal_string(totals["net"]), decimal_string(totals["tax"]),
                         decimal_string(totals["gross"]), invoice.currency])
    return out.getvalue()
