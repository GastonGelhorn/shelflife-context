import tempfile
import unittest

from invoicer.errors import NotFound
from invoicer.models import Customer, Invoice, LineItem
from invoicer.storage import JsonStorage


class StorageTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = JsonStorage(folder)
            invoice = Invoice("inv-1", "c-1", lines=[LineItem("a", 1, 100, 2300)])
            storage.save(invoice)
            self.assertEqual(storage.load("inv-1"), invoice)
            self.assertEqual([i.id for i in storage.all()], ["inv-1"])

    def test_unknown_keys_are_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = JsonStorage(folder)
            (storage.root / "invoices" / "inv-2.json").write_text('{"id": "inv-2", "customer_id": "c", "legacy": 1}')
            self.assertEqual(storage.load("inv-2").id, "inv-2")

    def test_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(NotFound):
                JsonStorage(folder).load("nope")
            with self.assertRaises(NotFound):
                JsonStorage(folder).customer("nobody")

    def test_customers(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = JsonStorage(folder)
            storage.add_customer(Customer("c-9", "Nine", "DE", "n@example.com"))
            self.assertEqual(storage.customer("c-9").country, "DE")
