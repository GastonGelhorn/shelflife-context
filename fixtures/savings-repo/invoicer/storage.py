"""One JSON file per invoice, customers in one file (docs/adr/0002)."""

import json
from dataclasses import asdict
from pathlib import Path

from .errors import NotFound
from .models import Customer, Invoice


class JsonStorage:
    def __init__(self, root):
        self.root = Path(root)
        (self.root / "invoices").mkdir(parents=True, exist_ok=True)

    def _path(self, invoice_id):
        return self.root / "invoices" / f"{invoice_id}.json"

    def _write(self, path, data):
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(path)

    def load(self, invoice_id):
        try:
            data = json.loads(self._path(invoice_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise NotFound(f"invoice {invoice_id}") from None
        return Invoice.from_dict(data)

    def save(self, invoice):
        self._write(self._path(invoice.id), invoice.to_dict())

    def all(self):
        return [self.load(path.stem) for path in sorted((self.root / "invoices").glob("*.json"))]

    def _customers(self):
        path = self.root / "customers.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def customer(self, customer_id):
        customers = self._customers()
        if customer_id not in customers:
            raise NotFound(f"customer {customer_id}")
        return Customer(**customers[customer_id])

    def add_customer(self, customer):
        customers = self._customers()
        customers[customer.id] = asdict(customer)
        self._write(self.root / "customers.json", customers)
