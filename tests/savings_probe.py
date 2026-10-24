"""What the savings bench checks in the fixture repository after each session, run in a process of its own so
the agent's code is imported fresh:

  python3 -I tests/savings_probe.py s1|s2|s3 WORKSPACE [--known-flags a,b]

Prints {"checks": {name: true|false}, "errors": {name: message}, "features": [...]}. The service runs on a
throwaway data folder; nothing under the workspace's data/ is written."""

import json
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

BASE_FLAGS = ["pdf_export", "reminders"]


def suite(root):
    done = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."], cwd=root,
                          capture_output=True, text=True, timeout=300)
    if done.returncode:
        raise AssertionError(done.stderr[-400:])
    return True


def ident(value):
    """The id an api function returned, whether it gave the id, the invoice, or a dict."""
    return value if isinstance(value, str) else getattr(value, "id", None) or (value or {}).get("id")


class Service:
    def __init__(self, folder):
        from invoicer import api, audit, config, errors
        from invoicer.models import Customer
        from invoicer.storage import JsonStorage
        self.api, self.audit, self.config, self.errors = api, audit, config, errors
        self.storage = JsonStorage(folder)
        self.storage.add_customer(Customer("c-1", "Acme", "IE", "a@example.com", "IE1"))
        api.use_storage(self.storage)

    def flags(self, on):
        for name in self.config.FEATURES:
            self.config.FEATURES[name] = on

    def finalized(self, unit_cents=10000):
        invoice_id = self.api.create_invoice("c-1", [{"description": "Work", "quantity": 1, "unit_cents": unit_cents,
                                                      "tax_rate_bp": 2300, "discount_bp": 0}])
        self.api.finalize(invoice_id)
        return invoice_id

    def audited(self, *invoice_ids):
        return sum(1 for e in self.audit.entries() if e.get("invoice_id") in invoice_ids)


def run(checks, root):
    results, errors = {}, {}
    for name, check in checks:
        try:
            results[name] = bool(check())
        except Exception as exc:  # noqa: BLE001 - every failure is a failed check, with its reason
            results[name] = False
            errors[name] = f"{type(exc).__name__}: {exc}"[:300] or traceback.format_exc(limit=1)
    return results, errors


def s1(root, folder, known):
    defaults = {}
    service = None

    def setup():
        nonlocal service
        from invoicer import config
        defaults.update(config.FEATURES)
        service = Service(folder)
        return hasattr(service.api, "issue_credit_note")

    def flag_off_by_default():
        added = [k for k in defaults if k not in known]
        return added and not any(defaults[k] for k in added)

    def refused_while_off():
        service.flags(False)
        invoice_id = service.finalized()
        try:
            service.api.issue_credit_note(invoice_id, 5000, "goodwill")
        except service.errors.FeatureDisabled:
            return True
        return False

    def works():
        from invoicer.totals import invoice_totals
        service.flags(True)
        invoice_id = service.finalized()
        before = service.audited(invoice_id)
        note = service.storage.load(ident(service.api.issue_credit_note(invoice_id, 5000, "goodwill")))
        works.audit = service.audited(invoice_id, note.id) > before
        return getattr(note, "kind", None) == "credit_note" and getattr(note, "credits_invoice", None) == invoice_id \
            and note.status == "finalized" and invoice_totals(note)["gross"] < 0

    def draft_refused():
        service.flags(True)
        draft = service.api.create_invoice("c-1", [{"description": "Work", "quantity": 1, "unit_cents": 100,
                                                    "tax_rate_bp": 2300, "discount_bp": 0}])
        try:
            service.api.issue_credit_note(draft, 50, "nope")
        except service.errors.InvoicerError:
            return True
        return False

    return [("tests_pass", lambda: suite(root)), ("api_present", setup), ("decision_flag_off", flag_off_by_default),
            ("decision_flag_enforced", refused_while_off), ("credit_note_works", works),
            ("decision_audit", lambda: getattr(works, "audit", False)), ("draft_refused", draft_refused)]


def s2(root, folder, known):
    defaults = {}
    service = None

    def setup():
        nonlocal service
        from invoicer import config
        defaults.update(config.FEATURES)
        service = Service(folder)
        return hasattr(service.api, "record_payment") and hasattr(service.api, "outstanding")

    def flag_off_by_default():
        added = [k for k in defaults if k not in known]
        return added and not any(defaults[k] for k in added)

    def refused_while_off():
        service.flags(False)
        invoice_id = service.finalized()
        try:
            service.api.record_payment(invoice_id, 1000)
        except service.errors.FeatureDisabled:
            return True
        return False

    state = {}

    def partial():
        service.flags(True)
        invoice_id = state["id"] = service.finalized()  # 100.00 + 23% = 12300 cents
        before = service.audited(invoice_id)
        first = service.api.outstanding(invoice_id)
        service.api.record_payment(invoice_id, 5000)
        state["audited"] = service.audited(invoice_id) > before
        return first == 12300 and service.api.outstanding(invoice_id) == 7300

    def overpayment():
        invoice_id = state["id"]
        try:
            service.api.record_payment(invoice_id, 7301)
        except service.errors.Overpayment:
            return service.api.outstanding(invoice_id) == 7300
        return False

    def paid_off():
        service.api.record_payment(state["id"], 7300)
        return service.api.outstanding(state["id"]) == 0

    return [("tests_pass", lambda: suite(root)), ("api_present", setup), ("decision_flag_off", flag_off_by_default),
            ("decision_flag_enforced", refused_while_off), ("partial_payment_works", partial),
            ("decision_audit", lambda: state.get("audited", False)), ("decision_overpayment", overpayment),
            ("paid_off", paid_off)]


def s3(root, folder, known):
    def total_matches_lines():
        from invoicer.render import line_gross
        from invoicer.storage import JsonStorage
        from invoicer.totals import invoice_totals
        shutil.copytree(Path(__file__).resolve().parent.parent / "fixtures" / "savings-repo" / "data", Path(folder) / "orig")
        invoice = JsonStorage(Path(folder) / "orig").load("inv-0042")
        return sum(line_gross(line) for line in invoice.lines) == invoice_totals(invoice)["gross"]

    def half_up_per_line():  # docs/adr/0003
        from invoicer.models import LineItem
        from invoicer.tax import line_tax
        return line_tax(LineItem("x", 1, 23450, 2100)) == 4925 and line_tax(LineItem("x", 1, 1999, 2300)) == 460

    return [("tests_pass", lambda: suite(root)), ("total_matches_lines", total_matches_lines),
            ("decision_record_followed", half_up_per_line)]


def main(argv):
    stage, root = argv[0], Path(argv[1]).resolve()
    known = argv[argv.index("--known-flags") + 1].split(",") if "--known-flags" in argv else BASE_FLAGS
    sys.path.insert(0, str(root))
    with tempfile.TemporaryDirectory() as folder:
        results, errors = run({"s1": s1, "s2": s2, "s3": s3}[stage](root, folder, known), root)
    try:
        from invoicer import config
        features = sorted(config.FEATURES)
    except Exception:  # noqa: BLE001 - a broken package already failed its checks
        features = []
    print(json.dumps({"checks": results, "errors": errors, "features": features}))


if __name__ == "__main__":
    main(sys.argv[1:])
