import unittest

from invoicer import api
from tests.helpers import line, temp_api


class NumberingTests(unittest.TestCase):
    def test_consecutive(self):
        with temp_api():
            first = api.create_invoice("c-1", [line()])
            second = api.create_invoice("c-1", [line()])
            self.assertEqual(api.finalize(first), "INV-2026-0001")
            self.assertEqual(api.finalize(second), "INV-2026-0002")

    def test_drafts_take_no_number(self):
        with temp_api():
            api.create_invoice("c-1", [line()])
            invoice_id = api.create_invoice("c-1", [line()])
            self.assertEqual(api.finalize(invoice_id), "INV-2026-0001")
