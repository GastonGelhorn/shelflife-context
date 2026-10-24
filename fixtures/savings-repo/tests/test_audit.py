import tempfile
import unittest
from pathlib import Path

from invoicer import audit


class AuditTests(unittest.TestCase):
    def test_record_and_read(self):
        with tempfile.TemporaryDirectory() as folder:
            audit.configure(Path(folder) / "audit.log")
            audit.record("void", "inv-1", reason="duplicate")
            audit.record("void", "inv-2", reason="typo")
            self.assertEqual([e["invoice_id"] for e in audit.entries()], ["inv-1", "inv-2"])
            self.assertEqual(audit.entries("inv-1")[0]["reason"], "duplicate")
