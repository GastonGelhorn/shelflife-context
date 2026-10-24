"""Settings for the invoicing service."""

from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_CURRENCY = "EUR"
PAYMENT_TERMS_DAYS = 30

# Features that are not on for every customer yet.
FEATURES = {
    "pdf_export": False,
    "reminders": False,
}


def enabled(name):
    return FEATURES.get(name, False)
