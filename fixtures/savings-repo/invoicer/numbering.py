def next_number(storage, year):
    """INV-<year>-<4 digits>, one more than the highest used this year."""
    prefix = f"INV-{year}-"
    used = [int(invoice.number[len(prefix):]) for invoice in storage.all() if invoice.number.startswith(prefix)]
    return f"{prefix}{max(used, default=0) + 1:04d}"
