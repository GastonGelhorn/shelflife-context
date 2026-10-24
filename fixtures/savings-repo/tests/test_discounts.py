import unittest

from invoicer.discounts import line_discount, line_net
from invoicer.errors import InvalidAmount
from invoicer.models import LineItem


class DiscountTests(unittest.TestCase):
    def test_no_discount(self):
        self.assertEqual(line_net(LineItem("a", 2, 1000, 2300)), 2000)

    def test_percentage_rounded_half_up(self):
        line = LineItem("a", 3, 4999, 2100, discount_bp=1000)
        self.assertEqual(line_discount(line), 1500)
        self.assertEqual(line_net(line), 13497)

    def test_out_of_range(self):
        with self.assertRaises(InvalidAmount):
            line_discount(LineItem("a", 1, 1000, 2300, discount_bp=12000))
