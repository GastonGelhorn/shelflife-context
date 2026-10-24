"""v0.11: a packet carries what helps the prompt it rides on, and nothing on text nobody typed."""

from pathlib import Path
import tempfile
import unittest

from shelflife_context.adapters import hook_response, packet_of
from shelflife_context.capture import segments
from shelflife_context.common import canonical, timestamp, timestamp_offset
from shelflife_context.compiler import Compiler
from shelflife_context.store import Store
from shelflife_context.turns import typed_text
from tests.fakes import FakeJudge


class TypedTextTests(unittest.TestCase):
    def test_host_blocks_are_not_typed(self):
        self.assertEqual(typed_text("<task-notification>\n<task-id>b1</task-id> done</task-notification>"), "")
        self.assertEqual(typed_text("<system-reminder>todo list</system-reminder>\nwhy is the deploy red?"), "why is the deploy red?")
        self.assertEqual(typed_text("<local-command-stdout>Set model to opus</local-command-stdout>"), "")
        self.assertEqual(typed_text("This session is being continued from a previous conversation that ran out of context."), "")

    def test_what_the_person_sent_stays(self):
        self.assertEqual(typed_text("<command-name>/loop</command-name><command-args>check the deploy</command-args>"), "check the deploy")
        pasted = "<pasted_content>\nTraceback ...\n</pasted_content>\nwhat broke?"
        self.assertEqual(typed_text(pasted), pasted)

    def test_segments_drop_host_text(self):
        authored, quoted = segments("<system-reminder>Mi manager es Dani</system-reminder>\nMi salario es 42000")
        self.assertEqual((authored, quoted), ("Mi salario es 42000", ""))


class QuietPacketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.now = "2026-10-24T12:00:00+00:00"
        self.store = Store(self.workspace / "memory.sqlite", clock=lambda: timestamp(self.now), create=True)
        self.store.remember("deploy", "pipeline", "GitHub Actions", canonical("GitHub Actions"))
        self.store.remember("user", "manager", "Dani", canonical("Dani"))
        self.judge = FakeJudge(rank=lambda query, line, question: 0.9)
        self.compiler = Compiler(self.store, jev=self.judge)
        self.turns = 0

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def prompt(self, text):
        self.now = timestamp_offset(self.now, 60)
        self.turns += 1
        event = {"cwd": str(self.workspace), "prompt": text, "session_id": "s1", "prompt_id": f"p{self.turns}"}
        response, _ = hook_response(event, self.workspace, self.store, self.compiler, strategy="jev", judge=self.judge)
        return packet_of(response["hookSpecificOutput"]["additionalContext"])

    def ranked(self):
        return [c for c in self.judge.calls if c[0] == "rank"]

    def test_a_task_notification_gets_the_token_and_no_claims(self):
        packet = self.prompt("<task-notification>\nBackground deploy pipeline finished; manager notified.\n</task-notification>")
        self.assertTrue(packet["turn"]["token"])
        self.assertEqual(packet.get("claims", []), [])
        self.assertEqual(self.ranked(), [])

    def test_a_continuation_summary_gets_no_claims(self):
        packet = self.prompt("This session is being continued from a previous conversation that ran out of context. "
                             "The deploy pipeline and the manager were discussed.")
        self.assertEqual(packet.get("claims", []), [])
        self.assertEqual(self.store.turn("s1", "p1")["origin"], "unknown")

    def test_the_judge_reads_only_what_was_typed(self):
        packet = self.prompt("<system-reminder>The manager is Dani.</system-reminder>\nwhich pipeline runs the deploy?")
        self.assertTrue(packet.get("claims"))
        self.assertEqual([c[1] for c in self.ranked()], ["which pipeline runs the deploy?"])

    def test_pasted_text_with_a_question_is_still_served(self):
        packet = self.prompt("<pasted_content>\nError: deploy pipeline failed at step 3\n</pasted_content>\nwhy?")
        self.assertTrue(packet.get("claims"))


if __name__ == "__main__":
    unittest.main()
