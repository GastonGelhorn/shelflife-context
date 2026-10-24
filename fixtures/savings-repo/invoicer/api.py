"""What the web layer calls. Every function takes ids and returns plain data."""

import datetime
import uuid

from . import audit, clock, config, notifications
from .errors import InvalidAmount, InvalidState
from .models import DRAFT, FINALIZED, VOID, Invoice, LineItem
from .money import format_amount
from .numbering import next_number
from .render import render_text
from .storage import JsonStorage
from .totals import invoice_totals

_storage = None


def use_storage(storage):
    global _storage
    _storage = storage
    audit.configure(storage.root / "audit.log")


def storage():
    if _storage is None:
        use_storage(JsonStorage(config.DATA_DIR))
    return _storage


def create_invoice(customer_id, lines=(), currency=config.DEFAULT_CURRENCY):
    storage().customer(customer_id)  # raises NotFound for an unknown customer
    invoice = Invoice(id="inv-" + uuid.uuid4().hex[:8], customer_id=customer_id, currency=currency,
                      lines=[LineItem(**line) for line in lines])
    storage().save(invoice)
    return invoice.id


def add_line(invoice_id, description, quantity, unit_cents, tax_rate_bp, discount_bp=0):
    invoice = storage().load(invoice_id)
    if invoice.status != DRAFT:
        raise InvalidState("only drafts take new lines")
    if quantity <= 0 or unit_cents < 0:
        raise InvalidAmount("quantity must be positive and price not negative")
    invoice.lines.append(LineItem(description, quantity, unit_cents, tax_rate_bp, discount_bp))
    storage().save(invoice)


def finalize(invoice_id):
    invoice = storage().load(invoice_id)
    if invoice.status != DRAFT:
        raise InvalidState(f"invoice is {invoice.status}")
    if not invoice.lines:
        raise InvalidState("an invoice needs at least one line")
    today = clock.today()
    invoice.number = next_number(storage(), today.year)
    invoice.issued_on = today.isoformat()
    invoice.due_on = (today + datetime.timedelta(days=config.PAYMENT_TERMS_DAYS)).isoformat()
    invoice.status = FINALIZED
    storage().save(invoice)
    return invoice.number


def void(invoice_id, reason):
    invoice = storage().load(invoice_id)
    if invoice.status != FINALIZED:
        raise InvalidState("only finalized invoices can be voided")
    invoice.status = VOID
    storage().save(invoice)
    audit.record("void", invoice_id, reason=reason)


def view(invoice_id):
    invoice = storage().load(invoice_id)
    totals = invoice_totals(invoice)
    return {"id": invoice.id, "number": invoice.number, "status": invoice.status, "customer_id": invoice.customer_id,
            "issued_on": invoice.issued_on, "due_on": invoice.due_on,
            **{name: format_amount(cents, invoice.currency) for name, cents in totals.items()}}


def text(invoice_id):
    invoice = storage().load(invoice_id)
    return render_text(invoice, storage().customer(invoice.customer_id))


def send(invoice_id):
    invoice = storage().load(invoice_id)
    if invoice.status != FINALIZED:
        raise InvalidState("only finalized invoices are sent")
    notifications.send_invoice(invoice, storage().customer(invoice.customer_id), text(invoice_id))
