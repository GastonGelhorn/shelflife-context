"""Who changed what: one JSON line per change, in audit.log beside the data."""

import datetime
import json
from pathlib import Path

_log = None


def configure(path):
    global _log
    _log = Path(path)


def record(action, invoice_id, **details):
    if _log is None:
        raise RuntimeError("audit log not configured; call api.use_storage first")
    entry = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
             "action": action, "invoice_id": invoice_id, **details}
    with _log.open("a", encoding="utf-8") as log:
        log.write(json.dumps(entry, sort_keys=True) + "\n")


def entries(invoice_id=None):
    if _log is None or not _log.exists():
        return []
    rows = [json.loads(line) for line in _log.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [row for row in rows if invoice_id is None or row["invoice_id"] == invoice_id]
