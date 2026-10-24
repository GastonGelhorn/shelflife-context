"""The outbox the mailer drains. Nothing here sends mail."""

OUTBOX = []


def send_invoice(invoice, customer, body):
    OUTBOX.append({"to": customer.email, "subject": f"Invoice {invoice.number}", "body": body})
