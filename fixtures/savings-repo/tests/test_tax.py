import unittest

from invoicer.errors import InvalidAmount
from invoicer.models import LineItem
from invoicer.tax import line_tax, rate_for


class TaxTests(unittest.TestCase):
    def test_rates(self):
        self.assertEqual(rate_for("IE"), 2300)
        self.assertEqual(rate_for("FR", reduced=True), 550)
        with self.assertRaises(InvalidAmount):
            rate_for("XX")

    def test_line_tax(self):
        self.assertEqual(line_tax(LineItem("a", 1, 10000, 2300)), 2300)
        self.assertEqual(line_tax(LineItem("a", 4, 2500, 2100)), 2100)

    def test_line_tax_after_discount(self):
        self.assertEqual(line_tax(LineItem("a", 1, 20000, 2000, discount_bp=5000)), 2000)
