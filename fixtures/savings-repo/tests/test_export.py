import unittest

from invoicer.export.csv_export import to_csv
from invoicer.models import Invoice, LineItem


class ExportTests(unittest.TestCase):
    def test_numbered_invoices_only(self):
        numbered = Invoice("i1", "c-1", number="INV-2026-0002", issued_on="2026-10-01", status="finalized",
                           lines=[LineItem("a", 1, 10000, 2300)])
        draft = Invoice("i2", "c-1", lines=[LineItem("a", 1, 10000, 2300)])
        rows = to_csv([numbered, draft]).splitlines()
        self.assertEqual(rows[0], "number,customer_id,issued_on,status,net,tax,gross,currency")
        self.assertEqual(rows[1:], ["INV-2026-0002,c-1,2026-10-01,finalized,100.00,23.00,123.00,EUR"])
