import unittest

from invoicer.money import apply_rate, decimal_string, format_amount, parse_amount, round_half_up


class MoneyTests(unittest.TestCase):
    def test_round_half_up(self):
        self.assertEqual(round_half_up(5, 10), 1)
        self.assertEqual(round_half_up(4, 10), 0)
        self.assertEqual(round_half_up(-5, 10), -1)
        self.assertEqual(round_half_up(15, 10), 2)

    def test_apply_rate(self):
        self.assertEqual(apply_rate(10000, 2300), 2300)
        self.assertEqual(apply_rate(999, 1000), 100)

    def test_format(self):
        self.assertEqual(format_amount(123450), "€1,234.50")
        self.assertEqual(format_amount(-300, "GBP"), "-£3.00")
        self.assertEqual(format_amount(5, "SEK"), "SEK 0.05")

    def test_decimal_string(self):
        self.assertEqual(decimal_string(123450), "1234.50")
        self.assertEqual(decimal_string(-7), "-0.07")

    def test_parse(self):
        self.assertEqual(parse_amount("12.5"), 1250)
        self.assertEqual(parse_amount("12,50"), 1250)
        self.assertEqual(parse_amount("-0.50"), -50)
        with self.assertRaises(ValueError):
            parse_amount("1.234")
