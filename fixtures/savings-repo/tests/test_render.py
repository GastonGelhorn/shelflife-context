import unittest

from invoicer.models import Customer, Invoice, LineItem
from invoicer.render import line_gross, render_text


class RenderTests(unittest.TestCase):
    def test_line_gross(self):
        self.assertEqual(line_gross(LineItem("a", 1, 10000, 2300)), 12300)

    def test_text(self):
        invoice = Invoice("i", "c", number="INV-2026-0007", due_on="2026-11-01",
                          lines=[LineItem("Widgets", 2, 5000, 2300, discount_bp=1000)])
        text = render_text(invoice, Customer("c", "Acme", "IE", "a@example.com", "IE1"))
        self.assertIn("Invoice INV-2026-0007", text)
        self.assertIn("VAT number: IE1", text)
        self.assertIn("2 x Widgets (-10%)", text)
        self.assertIn("€110.70", text)
        self.assertIn("Due on 2026-11-01", text)

    def test_draft(self):
        text = render_text(Invoice("i", "c"), Customer("c", "Acme", "IE", "a@example.com"))
        self.assertTrue(text.startswith("Invoice (draft)"))
