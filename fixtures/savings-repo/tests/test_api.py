import unittest

from invoicer import api, audit, notifications
from invoicer.errors import InvalidState, NotFound
from tests.helpers import line, temp_api


class ApiTests(unittest.TestCase):
    def test_draft_to_finalized(self):
        with temp_api():
            invoice_id = api.create_invoice("c-1", [line()])
            api.add_line(invoice_id, "Setup", 1, 5000, 2300)
            api.finalize(invoice_id)
            view = api.view(invoice_id)
            self.assertEqual((view["status"], view["gross"], view["due_on"]), ("finalized", "€184.50", "2026-10-31"))

    def test_finalized_takes_no_lines(self):
        with temp_api():
            invoice_id = api.create_invoice("c-1", [line()])
            api.finalize(invoice_id)
            with self.assertRaises(InvalidState):
                api.add_line(invoice_id, "Late", 1, 100, 2300)

    def test_unknown_customer(self):
        with temp_api():
            with self.assertRaises(NotFound):
                api.create_invoice("c-404")

    def test_void_is_audited(self):
        with temp_api():
            invoice_id = api.create_invoice("c-1", [line()])
            api.finalize(invoice_id)
            api.void(invoice_id, "duplicate")
            self.assertEqual(api.view(invoice_id)["status"], "void")
            self.assertEqual([e["action"] for e in audit.entries(invoice_id)], ["void"])

    def test_send(self):
        with temp_api():
            notifications.OUTBOX.clear()
            invoice_id = api.create_invoice("c-2", [line(tax_rate_bp=2100)])
            api.finalize(invoice_id)
            api.send(invoice_id)
            self.assertEqual(notifications.OUTBOX[0]["to"], "b@example.com")
            self.assertIn("Total", notifications.OUTBOX[0]["body"])
