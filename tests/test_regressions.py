import os
from pathlib import Path
import tempfile
import unittest

from shelflife_context.common import KernelError, canonical, timestamp
from shelflife_context.compiler import Compiler
from shelflife_context.demo import run
from shelflife_context.planner import Need, NeedPlan
from shelflife_context.store import Store


class RegressionTests(unittest.TestCase):
    def test_disposable_end_to_end_demo(self):
        self.assertTrue(run()["passed"])

    def test_temporal_and_discretion_variants(self):
        # 120 deterministic scenarios: 15 variations of 8 checks, not a model benchmark.
        for variant in range(15):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                store = Store(Path(directory) / "memory.sqlite", create=True, clock=lambda: timestamp("2026-10-05T12:00:00Z"))
                try:
                    compiler = Compiler(store)
                    first = store.remember(f"project_{variant}", "project_status", "Waiting", "Not started", valid_from="2026-10-01")
                    second = store.correct(first["id"], "Complete", "Completed today")
                    self.assertEqual(store.records()[0]["id"], second["id"])
                    self.assertEqual(store.records("2026-10-02")[0]["id"], first["id"])
                    self.assertEqual(compiler.project("Explain SQLite.").content, "")
                    projection = compiler.project("My project status")
                    self.assertIn(second["id"], projection.trace["selected"])
                    self.assertNotIn(first["id"], projection.trace["selected"])
                    store.remember("draft", "project_status", "Guessed", "Maybe complete", assertion_kind="hypothesis")
                    self.assertNotIn("Guessed", compiler.project("My project status").content)
                    missing = compiler.project("My schedule", plan=NeedPlan((Need(("availability",)),), "oracle"))
                    self.assertIn("missing_critical_evidence", missing.trace["warnings"])
                    store.forget(first["id"])
                    self.assertNotIn("Complete", canonical(store.records(history=True)))
                finally:
                    store.close()

    def test_relation_cycles_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "memory.sqlite", create=True)
            try:
                for entity in ("task", "project", "company"):
                    store.remember(entity, "label", entity, entity)
                store.relate("task", "project")
                store.relate("project", "company")
                with self.assertRaisesRegex(KernelError, "cycle"):
                    store.relate("company", "task")
            finally:
                store.close()

    def test_historical_projection_can_be_revalidated(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "memory.sqlite", create=True, clock=lambda: timestamp("2026-10-05"))
            try:
                row = store.remember("user", "manager", "Alex", "Alex", valid_from="2026-10-01")
                store.correct(row["id"], "Blair", "Blair")
                compiler = Compiler(store)
                projection = compiler.project("My manager", strategy="fts", as_of="2026-10-02")
                compiler.revalidate(projection)
                self.assertIn("Alex", projection.content)
            finally:
                store.close()

    def test_new_conflict_invalidates_prepared_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "memory.sqlite", create=True)
            try:
                store.remember("user", "salary", 42000, "Annual salary")
                compiler = Compiler(store)
                projection = compiler.project("My salary")
                store.remember("user", "salary", 52000, "Conflicting salary")
                with self.assertRaises(KernelError):
                    compiler.revalidate(projection)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()



class CompatMatrixClientTests(unittest.TestCase):
    """2026-10-19: a scheduled run inside the desktop app found nvm's stale claude 2.1.47 first, ran it with
    node 10, and the sessions it did start refused to run inside the parent session (CLAUDECODE)."""

    def fake(self, folder, text):
        folder.mkdir(parents=True, exist_ok=True)
        script = folder / "claude"
        script.write_text(f"#!/bin/sh\necho '{text}'\n")
        script.chmod(0o755)
        return script

    def test_newest_working_client_wins_over_path_order(self):
        from tests.compat_matrix import newest
        with tempfile.TemporaryDirectory() as tmp:
            old = self.fake(Path(tmp, "nvm"), "2.1.47 (Claude Code)")
            new = self.fake(Path(tmp, "brew"), "2.1.287 (Claude Code)")
            broken = Path(tmp, "broken", "claude")
            broken.parent.mkdir()
            broken.write_text("#!/bin/sh\nexit 1\n")
            broken.chmod(0o755)
            path = os.pathsep.join([str(broken.parent), str(old.parent), str(new.parent), "/usr/bin:/bin"])
            self.assertEqual(newest("claude", path=path), (str(new), "2.1.287 (Claude Code)"))

    def test_client_folder_goes_first_and_parent_session_is_dropped(self):
        from tests.compat_matrix import client_env
        base = {"PATH": "/old/node/bin:/usr/bin:/brew/bin", "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "x",
                "CLAUDE_PLUGIN_ROOT": "/p", "CLAUDE_CONFIG_DIR": "/c", "HOME": "/h"}
        env = client_env("/brew/bin/claude", None, base=base)
        self.assertEqual(env["PATH"].split(os.pathsep), ["/brew/bin", "/old/node/bin", "/usr/bin"])
        self.assertEqual({k for k in env if k != "PATH"}, {"CLAUDE_CONFIG_DIR", "HOME"})
