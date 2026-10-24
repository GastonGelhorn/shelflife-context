import contextlib
import datetime
import tempfile

from invoicer import api, clock
from invoicer.models import Customer
from invoicer.storage import JsonStorage


@contextlib.contextmanager
def temp_api(day=datetime.date(2026, 10, 1)):
    """The service on a throwaway data folder, with two customers and the date pinned."""
    with tempfile.TemporaryDirectory() as folder:
        storage = JsonStorage(folder)
        storage.add_customer(Customer("c-1", "Acme", "IE", "a@example.com", "IE1"))
        storage.add_customer(Customer("c-2", "Brava", "ES", "b@example.com"))
        api.use_storage(storage)
        clock.freeze(day)
        try:
            yield storage
        finally:
            clock.freeze(None)


def line(description="Item", quantity=1, unit_cents=10000, tax_rate_bp=2300, discount_bp=0):
    return {"description": description, "quantity": quantity, "unit_cents": unit_cents,
            "tax_rate_bp": tax_rate_bp, "discount_bp": discount_bp}
