import datetime

_frozen = None


def today():
    return _frozen or datetime.date.today()


def freeze(day):
    """Tests pin the date; None unpins it."""
    global _frozen
    _frozen = day
