import unittest

from invoicer.models import Invoice, LineItem
from invoicer.totals import invoice_totals, line_totals


class TotalsTests(unittest.TestCase):
    def test_line(self):
        self.assertEqual(line_totals(LineItem("a", 2, 5000, 2300)), {"net": 10000, "tax": 2300, "gross": 12300})

    def test_invoice(self):
        invoice = Invoice("i", "c", lines=[LineItem("a", 1, 10000, 2300), LineItem("b", 3, 1000, 1350)])
        self.assertEqual(invoice_totals(invoice), {"net": 13000, "tax": 2705, "gross": 15705})

    def test_empty(self):
        self.assertEqual(invoice_totals(Invoice("i", "c")), {"net": 0, "tax": 0, "gross": 0})
