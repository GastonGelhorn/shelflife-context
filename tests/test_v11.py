"""v0.11: a packet carries what helps the prompt it rides on, and nothing on text nobody typed."""

from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import io
import json
import os
import tempfile
import time
import unittest

from shelflife_context import cli

from shelflife_context.adapters import hook_response, packet_of, stop_response
from shelflife_context.capture import locate_quote, segments
from shelflife_context.common import canonical, timestamp, timestamp_offset
from shelflife_context.compiler import READER_RULES, Compiler
from shelflife_context.mcp import INSTRUCTIONS, Server
from shelflife_context.report import transcripts
from shelflife_context.store import Store
from shelflife_context.turns import typed_text
from tests.fakes import FakeJudge, answers

HOST = [4242, "Sat Oct 24 12:00:00 2026"]


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

    def test_a_claim_the_session_holds_is_not_sent_again_until_it_restarts(self):
        from shelflife_context.adapters import session_start_response
        first = self.prompt("which pipeline runs the deploy?")
        self.assertTrue([c for c in first["claims"] if c["entity"] == "deploy"])
        again = self.prompt("and the deploy pipeline for staging?")
        self.assertFalse([c for c in again.get("claims", []) if c["entity"] == "deploy"])
        self.assertIn("delivered_earlier", self.store.trace(self.store.turn("s1", "p2")["projection_id"])["excluded"].values())
        session_start_response({"cwd": str(self.workspace), "hook_event_name": "SessionStart", "session_id": "s1",
                                "source": "compact"}, self.workspace, self.store)
        self.now = timestamp_offset(self.now, 1)
        after = self.prompt("the deploy pipeline again?")
        self.assertTrue([c for c in after["claims"] if c["entity"] == "deploy"])

    def test_pasted_text_with_a_question_is_still_served(self):
        packet = self.prompt("<pasted_content>\nError: deploy pipeline failed at step 3\n</pasted_content>\nwhy?")
        self.assertTrue(packet.get("claims"))


class FallbackTests(unittest.TestCase):
    """When the judge fails, keyword matches are all there is: few, and about what the prompt names."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "memory.sqlite", create=True)
        self.compiler = Compiler(self.store, jev=FakeJudge(fail="down"))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def add(self, entity, predicate, value):
        return self.store.remember(entity, predicate, value, canonical(value))

    def test_a_word_in_a_value_is_not_enough(self):
        pipeline = self.add("deploy", "pipeline", "GitHub Actions")
        for n in range(5):
            self.add(f"note{n}", "summary", f"talked about the deploy and the release on day {n}")
        projection = self.compiler.project("how do we deploy?", strategy="jev")
        self.assertIn("jev_unavailable", projection.trace["warnings"])
        self.assertEqual(projection.trace["selected"], [pipeline["id"]])

    def test_at_most_two_matches(self):
        for predicate in ("pipeline", "region", "owner", "schedule"):
            self.add("deploy", predicate, "x")
        projection = self.compiler.project("how do we deploy?", strategy="jev")
        self.assertEqual(len(projection.trace["selected"]), 2)


class JudgeLoadTests(unittest.TestCase):
    def test_the_judge_gets_what_fits_in_its_seconds_likely_pairs_first(self):
        from shelflife_context.planner import DEFAULT_PAIR_SECONDS, JUDGE_SECONDS
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "memory.sqlite", create=True)
            try:
                for n in range(30):
                    store.remember(f"service{n}", "status", "deploy pending", canonical("deploy pending"))
                store.remember("deploy", "pipeline", "GitHub Actions", canonical("GitHub Actions"))
                judge = FakeJudge(rank=lambda query, line, question: 0.9)
                Compiler(store, jev=judge).project("which pipeline runs the deploy?", strategy="jev")
                judged = [line for call in judge.calls if call[0] == "rank" for line in call[2]]
                self.assertEqual(len(judged), int(JUDGE_SECONDS / DEFAULT_PAIR_SECONDS))
                self.assertTrue(judged[0].startswith("deploy pipeline"))
            finally:
                store.close()


class CaptureCostTests(unittest.TestCase):
    """Asking for a capture costs requests: ask only when sure, never hand the turn back unasked, re-ask once."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.now = "2026-10-24T12:00:00+00:00"
        self.store = Store(self.workspace / "memory.sqlite", clock=lambda: timestamp(self.now), create=True)
        self.judge = FakeJudge()
        self.compiler = Compiler(self.store)
        self.turns = 0

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def prompt(self, text):
        self.now = timestamp_offset(self.now, 60)
        self.turns += 1
        event = {"cwd": str(self.workspace), "prompt": text, "session_id": "s1", "prompt_id": f"p{self.turns}"}
        response, _ = hook_response(event, self.workspace, self.store, self.compiler, judge=self.judge)
        self.store.register_session("s1", "claude", [HOST])
        return response["hookSpecificOutput"]["additionalContext"], event

    def stop(self, event):
        self.now = timestamp_offset(self.now, 2)
        return stop_response({"cwd": str(self.workspace), "hook_event_name": "Stop", "session_id": "s1",
                              "prompt_id": event["prompt_id"], "last_assistant_message": "Ok."}, self.workspace, self.store, self.judge)

    def tools_running(self):
        server = Server(self.store, self.compiler, judge=self.judge, parent=HOST)
        server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {}}})
        server.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"})
        server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_status", "arguments": {}}})

    def test_the_turn_is_not_handed_back_unless_the_owner_asks(self):
        self.tools_running()
        context, event = self.prompt("Mi manager es Dani")
        self.assertIn("memory_capture", context)
        self.assertNotIn("decision", self.stop(event))
        self.store.set_policy({"stop_nudge": True})
        _, event = self.prompt("El deadline es el viernes")
        self.assertEqual(self.stop(event)["decision"], "block")

    def test_an_unsure_count_asks_for_nothing(self):
        def unsure(qid, state, spec):
            if qid == "count":
                return {"none": 0.1, "one": 0.9, "two": 0.0, "several": 0.0}
            return answers()(qid, state, spec)
        self.judge.ask_fn = unsure
        context, event = self.prompt("Creo que Dani lleva el proyecto")
        self.assertNotIn("memory_capture", context)
        turn = self.store.turn("s1", event["prompt_id"])
        self.assertEqual(turn["gate_count"], 0)
        self.assertIn("gate_unsure", turn["flags"])

    def test_the_request_rides_along_another_tool_call(self):
        context, _ = self.prompt("Mi manager es Dani")
        self.assertIn("in the same message as your first other tool call", context)

    def test_no_more_facts_are_asked_for_than_the_caps_allow(self):
        self.judge.ask_fn = answers(count="several")
        self.store.set_policy({"caps": {"turn": 2, "session": 10, "day": 30}})
        context, event = self.prompt("Mi manager es Dani, el deadline es el viernes y usamos pnpm")
        self.assertEqual(packet_of(context)["turn"]["capture"]["facts_stated"], 2)
        self.store.set_policy({"caps": {"turn": 2, "session": 0, "day": 30}})
        context, event = self.prompt("El staging se llama checkout-stg")
        self.assertNotIn("capture", packet_of(context)["turn"])
        self.assertIn("gate_capped", self.store.turn("s1", event["prompt_id"])["flags"])

    def test_an_unsaved_fact_is_offered_again_once(self):
        self.prompt("Mi manager es Dani")
        self.assertEqual(packet_of(self.prompt("ok")[0])["turn"]["pending"][0]["facts_not_captured"], 1)
        self.assertNotIn("pending", packet_of(self.prompt("sigue")[0])["turn"])

    def test_captured_claims_are_described_as_what_they_are(self):
        self.assertFalse([r for r in READER_RULES if "kernel's reading" in r])
        self.assertNotIn("readings", INSTRUCTIONS)


DECISIONS = ("Three decisions for this codebase, from now on:\n"
             "1. Every new feature goes behind a flag in `invoicer/config.py` `FEATURES`, off by default; while its flag is off, "
             "the feature's api functions raise `errors.FeatureDisabled`.\n"
             "2. Every function in `api.py` that changes an invoice writes an audit entry with `audit.record(action, invoice_id, ...)`.\n"
             "3. When we build partial payments, an overpayment is rejected with a new `errors.Overpayment`; we never keep it as credit.")


class StatedDecisionsTests(unittest.TestCase):
    """Three decisions stated in one message are all saved, and a later message gets them as claims."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.now = "2026-10-25T12:00:00+00:00"
        self.store = Store(self.workspace / "memory.sqlite", clock=lambda: timestamp(self.now), create=True)
        self.judge = FakeJudge(rank=lambda query, line, question: 0.9, ask=answers(category="constraints", count="several"))
        self.compiler = Compiler(self.store, jev=self.judge)
        self.turns = 0

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def prompt(self, text):
        self.now = timestamp_offset(self.now, 60)
        self.turns += 1
        event = {"cwd": str(self.workspace), "prompt": text, "session_id": f"s{self.turns}", "prompt_id": f"p{self.turns}"}
        response, _ = hook_response(event, self.workspace, self.store, self.compiler, strategy="jev", judge=self.judge)
        self.store.register_session(event["session_id"], "claude", [HOST])
        return response["hookSpecificOutput"]["additionalContext"]

    def capture(self, context, facts):
        server = Server(self.store, self.compiler, judge=self.judge, parent=HOST)
        server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {}}})
        server.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"})
        response = server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_capture",
                                    "arguments": {"token": packet_of(context)["turn"]["token"], "facts": facts}}})
        return response["result"]["structuredContent"]["results"]

    def test_a_quote_may_cross_a_semicolon_or_a_full_stop(self):
        cited = locate_quote("an overpayment is rejected with a new `errors.Overpayment`; we never keep it as credit.", DECISIONS)
        self.assertTrue(cited.startswith("When we build") and cited.endswith("keep it as credit."))
        self.assertTrue(locate_quote("Ship on Friday. Not before.", "Plan: ship on Friday. Not before. Thanks."))
        self.assertIsNone(locate_quote("ship on Monday", "Plan: ship on Friday. Not before."))

    def test_three_decisions_in_one_message_are_all_saved(self):
        context = self.prompt(DECISIONS)
        results = self.capture(context, [
            {"entity": "invoicer", "predicate": "feature_flag_policy", "value": "new features behind an off-by-default flag",
             "quote": "Every new feature goes behind a flag in `invoicer/config.py` `FEATURES`, off by default; while its flag is "
                      "off, the feature's api functions raise `errors.FeatureDisabled`."},
            {"entity": "invoicer", "predicate": "audit_policy", "value": "invoice changes write an audit entry",
             "quote": "Every function in `api.py` that changes an invoice writes an audit entry"},
            {"entity": "invoicer", "predicate": "overpayment_policy", "value": "overpayment rejected with errors.Overpayment",
             "quote": "an overpayment is rejected with a new `errors.Overpayment`; we never keep it as credit."}])
        self.assertEqual([r["status"] for r in results], ["captured"] * 3)


if __name__ == "__main__":
    unittest.main()


class ReportTests(unittest.TestCase):
    """What a week of transcripts says memory cost: packets cached and re-read, memory-only requests, Stop hand-backs."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "projects"
        (self.root / "repo").mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, n, usage, tools=(), sidechain=False):
        content = [{"type": "tool_use", "name": name} for name in tools] or [{"type": "text", "text": "ok"}]
        return {"type": "assistant", "isSidechain": sidechain,
                "message": {"id": f"m{n}", "model": "claude-sonnet-5-5", "usage": usage, "content": content}}

    def write(self, name, entries):
        path = self.root / "repo" / name
        path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        return path

    def test_three_costs_priced_at_the_session_model(self):
        body = "Shelflife " + "x" * 339_990  # 340,002 chars once quoted, about 100,000 tokens
        self.write("s1.jsonl", [
            {"type": "user", "message": {"content": "deploy staging"}},
            {"type": "attachment", "attachment": {"type": "hook_additional_context", "hookName": "UserPromptSubmit", "content": body}},
            self.call(1, {"input_tokens": 10}),
            self.call(2, {"input_tokens": 1_000_000}, ["mcp__plugin_shelflife-context_shelflife-context__memory_capture"]),
            self.call(9, {"input_tokens": 5_000_000}, sidechain=True),
            {"type": "attachment", "attachment": {"type": "hook_blocking_error", "hookEvent": "Stop", "content": "Shelflife: save it first"}},
            self.call(3, {"output_tokens": 100_000}),
            {"type": "user", "message": {"content": "thanks"}},
            self.call(4, {"cache_read_input_tokens": 1_000_000}),
        ])
        self.write("s2.jsonl", [{"type": "user", "message": {"content": "hi"}}, self.call(5, {"input_tokens": 500_000})])
        old = self.write("s3.jsonl", [self.call(6, {"input_tokens": 9_000_000})])
        os.utime(old, (time.time() - 10 * 86400,) * 2)
        result = transcripts(7, self.root)
        self.assertEqual((result["sessions"], result["requests"], result["sessions_with_shelflife"]), (2, 5, 1))
        self.assertEqual(result["sessions_usd"], 4.2)  # 2 + 1 + 0.2 in s1, 1 in s2; the sidechain and the old session left out
        self.assertEqual(result["packets"], {"count": 1, "tokens_each": 100001, "usd": 0.33})  # written once, re-read by 4 requests
        self.assertEqual(result["tools"], {"requests": 1, "usd": 2.0, "calls": {"memory_capture": 1}})
        self.assertEqual(result["stops"], {"handed_back": 1, "requests": 1, "usd": 1.0})
        self.assertEqual(result["shelflife_usd"], 3.33)
        self.assertEqual(result["share_of_their_sessions"], 1.0406)  # of s1's 3.20: the fixture's usage leaves the packet out

    def test_a_price_can_be_given_and_a_bad_one_is_refused(self):
        self.write("s1.jsonl", [self.call(1, {"input_tokens": 1_000_000})])
        db = str(Path(self.temp.name) / "memory.sqlite")
        Store(db, scope="work", create=True).close()
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--db", db, "--scope", "work", "report", "--transcripts", str(self.root),
                             "--price", "claude-sonnet-5-5=1,5,0.1,1.25"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["cost"]["sessions_usd"], 1.0)
        self.assertEqual(json.loads(out.getvalue())["selection"], {"packets": 0})
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(["--db", db, "--scope", "work", "report", "--price", "sonnet=1,2"]), 1)
        self.assertIn("MODEL=input,output", err.getvalue())
