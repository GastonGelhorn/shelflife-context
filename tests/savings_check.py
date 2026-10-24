"""Opt-in A/B bench: do jevmate and Shelflife save money in real Claude Code sessions, at equal correctness?

Each run copies `fixtures/savings-repo` (a small invoicing service with tests, decision records and one
planted rounding bug) into a disposable git repository and works it through three fresh headless `claude -p`
sessions in a row, sharing the repository and, where wired, the memory store:

  S1  the owner states three project decisions and asks for credit notes, with tests;
  S2  "add partial payments the way we agreed": the decisions are needed and nobody restates them;
  S3  a customer reports a one-cent difference; find the cause, fix it, add a test that would have caught it.

Variants differ only in what is wired in: none, jevmate (the plugin), shelflife (the kernel from this checkout,
with jev selection), both. Claude Code's own auto memory is off in all of them, so "none" carries nothing from
one session to the next but the code. Bash, Read, Edit, Write, Glob and Grep are allowed in every variant.

After every session the harness commits the work (as a person would) and `tests/savings_probe.py` checks it:
the suite passes, the feature works, and each decision was applied. Measured per session: total_cost_usd,
tokens, turns, wall time, tool calls, memory and jev calls (jev writes to a ledger of its own under the
workspace). The report gives, per variant and session, the mean and range over repetitions, the checks
passed, and the saving against "none", flagged as noise wherever the spread is larger than the difference.

  python3 -m tests.savings_check --workspace DIR [--repetitions 3] [--model claude-sonnet-5-5]

It uses the owner's Claude Code sign-in and quota. A repetition of four variants is twelve sessions."""

import argparse
import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from shelflife_context.adapters import configuration
from shelflife_context.common import canonical
from shelflife_context.store import Store
from tests.compare_check import JEV_TOOLS, OTHER_PLUGINS, kernel_summary, timed
from tests.native_claude_check import MEMORY_TOOLS
from tests.native_v06_check import git, wait_for_pass

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "savings-repo"
PROBE = Path(__file__).resolve().parent / "savings_probe.py"
TOOLS = "Read,Glob,Grep,Edit,Write,Bash"
TOOLS_OFF = "WebFetch,WebSearch,Agent,Task,NotebookEdit"
VARIANTS = {
    "none": {"kernel": False, "jevmate": False},
    "jevmate": {"kernel": False, "jevmate": True},
    "shelflife": {"kernel": True, "jevmate": False},
    "both": {"kernel": True, "jevmate": True},
}
SESSIONS = [
    ("s1", "Three decisions for this codebase, from now on:\n"
           "1. Every new feature goes behind a flag in `invoicer/config.py` `FEATURES`, off by default; while its flag "
           "is off, the feature's api functions raise `errors.FeatureDisabled`.\n"
           "2. Every function in `api.py` that changes an invoice writes an audit entry with "
           "`audit.record(action, invoice_id, ...)`.\n"
           "3. When we build partial payments, an overpayment is rejected with a new `errors.Overpayment`; we never "
           "keep it as credit.\n\n"
           "Now add credit notes: `api.issue_credit_note(invoice_id, amount_cents, reason)` creates a finalized invoice "
           "with `kind=\"credit_note\"`, one negative line for the amount, and `credits_invoice` set to the original "
           "invoice's id, and returns the credit note's id. Only finalized invoices can be credited. Add tests and run "
           "the suite."),
    ("s2", "Add partial payments the way we agreed: `api.record_payment(invoice_id, amount_cents)` and "
           "`api.outstanding(invoice_id)`, which returns what is still owed in cents. Add tests and run the suite."),
    ("s3", "A customer says invoice INV-2026-0042 shows a total one cent lower than the sum of its lines. Find the "
           "cause and fix it, with a test that would have caught it. Then tell me the cause in one sentence."),
]
# S3's answer names where the bug is and what it does.
CAUSE = ({"line_tax", "tax.py"}, {"floor", "trunc", "//", "round down", "rounds down", "rounded down", "integer division",
                                 "half up", "half-up"})


def jev_home(base):
    home = base / "jev-home"
    home.mkdir(parents=True, exist_ok=True)
    if not (home / "config.json").exists():
        for real in (os.environ.get("JEV_HOME"), Path.home() / ".config" / "jev", Path.home() / ".config" / "typesafe"):
            if real and (Path(real) / "config.json").exists():
                for name in ("config.json", "api_key"):  # the judge set up, nothing else
                    if (Path(real) / name).exists():
                        shutil.copy(Path(real) / name, home / name)
                break
    return home


def jevmate_server():
    """The installed jevmate plugin's MCP server, named as Claude Code names a plugin's server, so its tools keep
    the names the plugin gives them: --strict-mcp-config, which keeps the user's own servers out, drops it too."""
    installed = json.loads((Path.home() / ".claude" / "plugins" / "installed_plugins.json").read_text())
    entry = next(e for e in installed.get("plugins", installed)["jevmate@gastongelhorn"] if e.get("scope") == "user")
    root = entry["installPath"]
    servers = json.loads((Path(root) / ".mcp.json").read_text())["mcpServers"]
    expand = lambda value: value.replace("${CLAUDE_PLUGIN_ROOT}", root)  # noqa: E731
    return {"plugin_jevmate_" + name: {**config, "args": [expand(a) for a in config.get("args", [])],
                                       "env": {**config.get("env", {}), "CLAUDE_PLUGIN_ROOT": root}}
            for name, config in servers.items()}


def ledger(home):
    try:
        return [json.loads(line) for line in (home / "usage.jsonl").read_text().splitlines() if line.strip()]
    except OSError:
        return []


def prepare(root, variant, jev_command):
    """The fixture as a fresh git repository, wired for the variant."""
    shutil.copytree(FIXTURE, root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "Invoicing service")
    spec = VARIANTS[variant]
    settings = {"enabledPlugins": {name: False for name in OTHER_PLUGINS}}
    settings["enabledPlugins"]["jevmate@gastongelhorn"] = spec["jevmate"]
    database, mcp = root.parent / (root.name + "-memory.sqlite"), {"mcpServers": {}}
    hook_log = root.parent / (root.name + "-hooks.jsonl")
    if spec["kernel"]:
        Store(database, scope="pilot", create=True).close()
        hooks = configuration("claude", root, database, "pilot", strategy="jev", jev_command=jev_command)["config"]["hooks"]
        for event, groups in hooks.items():
            for group in groups:
                for hook in group["hooks"]:
                    hook["command"] = timed(hook["command"], hook_log, event)
        settings["hooks"] = hooks
        mcp = configuration("claude", root, database, "pilot", mode="mcp", strategy="jev", jev_command=jev_command)["config"]
    if spec["jevmate"]:
        mcp["mcpServers"].update(jevmate_server())
    (root / ".claude").mkdir(exist_ok=True)
    (root / ".claude" / "settings.local.json").write_text(json.dumps(settings, indent=2))
    (root / ".git" / "info" / "exclude").write_text(".claude/\n")  # the wiring is not the agent's work
    mcp_path = root.parent / (root.name + "-mcp.json")
    mcp_path.write_text(json.dumps(mcp, indent=2))
    allowed = [TOOLS] + ([MEMORY_TOOLS] if spec["kernel"] else []) + ([JEV_TOOLS] if spec["jevmate"] else [])
    return database if spec["kernel"] else None, mcp_path, hook_log, ",".join(allowed)


def environment(home):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE_PLUGIN_OPTION_", "JEV_SESSION", "JEV_AGENT", "JEV_GUARD"))
           and k not in {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT"}}
    env.update(SHELFLIFE_CONTEXT_OFF="1",  # the installed plugin stays out; the variant wires its own kernel
               ENABLE_CLAUDEAI_MCP_SERVERS="false", CLAUDE_CODE_DISABLE_AUTO_MEMORY="1",
               JEV_HOME=str(home), JEV_CLAUDE_SETTINGS=str(home / "claude-settings.json"))
    return env


def invoke(claude, root, prompt, mcp_path, allowed, model, env, max_turns):
    command = [claude, "-p", prompt, "--output-format", "stream-json", "--verbose", "--max-turns", str(max_turns),
               "--mcp-config", str(mcp_path), "--strict-mcp-config", "--disallowedTools", TOOLS_OFF, "--allowedTools", allowed]
    if model:
        command += ["--model", model]
    started = time.perf_counter()
    try:
        done = subprocess.run(command, cwd=root, stdin=subprocess.DEVNULL, text=True, capture_output=True, timeout=1800, env=env)
    except subprocess.TimeoutExpired:
        return {"exit_code": 124, "answer": None, "seconds": 1800.0, "tools": []}
    out = {"exit_code": done.returncode, "seconds": round(time.perf_counter() - started, 1), "tools": []}
    for line in done.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind, sub = event.get("type"), event.get("subtype")
        if kind == "system" and sub == "init":
            out["plugins"] = sorted(p["name"] for p in event.get("plugins", []) if p.get("path") != "builtin")
            out["mcp"] = sorted(m["name"] + ":" + m["status"] for m in event.get("mcp_servers", []))
        elif kind == "assistant":
            out["tools"] += [c["name"] for c in event["message"].get("content", []) if c.get("type") == "tool_use"]
        elif kind == "result":
            usage = event.get("usage") or {}
            out.update(answer=event.get("result"), is_error=event.get("is_error"), stop=event.get("subtype"),
                       cost_usd=event.get("total_cost_usd"), num_turns=event.get("num_turns"), api_ms=event.get("duration_api_ms"),
                       tokens={k: usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                                              "cache_read_input_tokens", "output_tokens")},
                       models=sorted(event.get("modelUsage") or {}))
    if done.returncode or out.get("is_error"):
        out["stderr"] = (done.stderr or "")[-800:]
    return out


def probe(root, stage, known=None):
    command = [sys.executable, "-I", str(PROBE), stage, str(root)] + (["--known-flags", ",".join(known)] if known else [])
    done = subprocess.run(command, capture_output=True, text=True, timeout=600)
    try:
        return json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"checks": {}, "errors": {"probe": (done.stderr or done.stdout)[-400:]}, "features": []}


def catches_the_bug(root, before):
    """S3's tests, run against the code as it was before S3: they should fail."""
    with tempfile.TemporaryDirectory() as folder:
        copy = Path(folder) / "repo"
        shutil.copytree(root, copy, ignore=shutil.ignore_patterns("__pycache__", ".claude"))
        git(copy, "checkout", before, "--", "invoicer")
        done = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."], cwd=copy,
                              capture_output=True, text=True, timeout=300)
        return done.returncode != 0


def commit(root, message):
    git(root, "add", "-A")
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True).stdout
    if status.strip():
        git(root, "commit", "-q", "-m", message)
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def run_variant(claude, base, variant, model, jev_command, home, max_turns, sessions):
    root = base / variant
    root.parent.mkdir(parents=True, exist_ok=True)
    database, mcp_path, hook_log, allowed = prepare(root, variant, jev_command)
    env, steps, known, signature = environment(home), [], None, None
    head = commit(root, "Invoicing service")
    for stage, prompt in [s for s in SESSIONS if s[0] in sessions]:
        before_ledger, before_head = len(ledger(home)), head
        result = invoke(claude, root, prompt, mcp_path, allowed, model, env, max_turns)
        calls = ledger(home)[before_ledger:]
        result["jev"] = {"calls": len(calls), "ms": sum(c.get("ms", 0) for c in calls),
                         "by_agent": {a: sum(1 for c in calls if c.get("agent") == a) for a in {c.get("agent") for c in calls}}}
        head = commit(root, f"Session {stage}")
        result["probe"] = probe(root, stage, known)
        known = result["probe"].get("features") or known
        if stage == "s3":
            answer = (result.get("answer") or "").casefold()
            result["probe"]["checks"]["regression_test"] = catches_the_bug(root, before_head)
            result["probe"]["checks"]["cause_named"] = any(t in answer for t in CAUSE[0]) and any(t in answer for t in CAUSE[1])
        result["stage"] = stage
        if database and stage == "s1":
            # The SessionStart hook only starts the repository pass; later sessions should see its result.
            signature = wait_for_pass(database, root, previous=signature, seconds=90) or signature
        steps.append(result)
        if result.get("is_error") and not any(result.get("tokens", {}).values()):
            # Nothing reached the model (sign-in, quota): every later session would fail the same way.
            raise Unusable(result.get("answer") or result.get("stderr") or "the session failed before any request")
        print(f"  {stage}: ${result.get('cost_usd') or 0:.3f} · {result.get('num_turns')} turns · {result['seconds']} s · "
              f"{sum(result['probe']['checks'].values())}/{len(result['probe']['checks'])} checks", file=sys.stderr, flush=True)
    hooks = [json.loads(line) for line in hook_log.read_text().splitlines()] if hook_log.exists() else []
    return {"variant": variant, "steps": steps, "hook_ms": hooks, "kernel": kernel_summary(database) if database else None}


class Unusable(Exception):
    pass


def preflight(claude, model):
    """One small request, so an expired sign-in stops the bench before twelve sessions report it. The user's
    plugins load here, so jev gets a home of its own that is thrown away."""
    with tempfile.TemporaryDirectory() as folder:
        done = subprocess.run([claude, "-p", "Reply with ok.", "--output-format", "json", "--model", model],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180, env=environment(Path(folder)))
    try:
        result = json.loads(done.stdout)
    except ValueError:
        return (done.stderr or done.stdout or "no answer").strip()[-300:]
    return result.get("result") if result.get("is_error") else None


def judge_elsewhere(home, jev_command):
    """Where jev would send a request from the bench's home, when that is off this machine: the kernel judges
    only locally, so such a bench would measure Shelflife with no judge at all."""
    from shelflife_context.judge import is_local
    done = subprocess.run([jev_command, "rank", "--dry-run", "--json", "--instructions", "Is `candidate` relevant to `query`?",
                           "--query", "q"], input="c\n", capture_output=True, text=True, timeout=60, env=environment(home))
    try:
        url = json.loads(done.stdout)["url"]
    except (ValueError, KeyError, TypeError):
        return (done.stderr or done.stdout or "jev did not answer").strip()[-300:]
    return None if is_local(url) else url


def spread(values):
    return {"mean": round(statistics.mean(values), 4), "min": round(min(values), 4), "max": round(max(values), 4)} if values else None


def summarize(runs, sessions):
    table = {}
    for variant in VARIANTS:
        mine = [r for r in runs if r["variant"] == variant]
        if not mine:
            continue
        row = {"runs": len(mine)}
        for stage in sessions + ["chain"]:
            steps = [s for r in mine for s in r["steps"] if stage == "chain" or s["stage"] == stage]
            per_run = [sum(s.get("cost_usd") or 0 for s in r["steps"] if stage == "chain" or s["stage"] == stage) for r in mine]
            checks = [v for s in steps for v in s["probe"]["checks"].values()]
            decisions = [v for s in steps for k, v in s["probe"]["checks"].items() if k.startswith("decision_")]
            row[stage] = {
                "cost_usd": spread(per_run),
                "checks_passed": f"{sum(checks)}/{len(checks)}",
                "decisions_applied": f"{sum(decisions)}/{len(decisions)}" if decisions else None,
                "turns": spread([s.get("num_turns") or 0 for s in steps]),
                "seconds": spread([s["seconds"] for s in steps]),
                "output_tokens": spread([s.get("tokens", {}).get("output_tokens", 0) for s in steps]),
                "context_tokens": spread([sum(v for k, v in s.get("tokens", {}).items() if k != "output_tokens") for s in steps]),
                "tool_calls": spread([len(s["tools"]) for s in steps]),
                "memory_calls": sum(sum(t.startswith("mcp__shelflife-context") for t in s["tools"]) for s in steps),
                "jev_tool_calls": sum(sum(t.startswith("mcp__plugin_jevmate") for t in s["tools"]) for s in steps),
                "judge_calls": sum(s.get("jev", {}).get("calls", 0) for s in steps),
                "errors": sum(bool(s["exit_code"] or s.get("is_error")) for s in steps),
            }
        hook = {}
        for r in mine:
            for h in r["hook_ms"]:
                hook.setdefault(h["event"], []).append(h["ms"])
        row["kernel_hook_median_ms"] = {e: round(statistics.median(v)) for e, v in hook.items()}
        table[variant] = row
    base = table.get("none")
    for variant, row in table.items():
        if not base or variant == "none":
            continue
        for stage in sessions + ["chain"]:
            mine, theirs = row[stage]["cost_usd"], base[stage]["cost_usd"]
            difference = theirs["mean"] - mine["mean"]
            noise = max(theirs["max"] - theirs["min"], mine["max"] - mine["min"])
            row[stage]["saving_vs_none"] = round(difference / theirs["mean"], 3) if theirs["mean"] else None
            row[stage]["within_noise"] = abs(difference) <= noise
    return table


def markdown(report):
    lines = [f"# Savings bench", "", f"{report['client']} · model {report['model']} · {len(report['runs'])} runs · "
             f"{report['started']}", ""]
    sessions = report["sessions"]
    lines += ["| Variant | " + " | ".join(s.upper() for s in sessions) + " | Chain | Saving vs none | Checks | Decisions in S2 |",
              "| --- |" + " --- |" * (len(sessions) + 4)]
    for variant, row in report["summary"].items():
        cells = [f"${row[s]['cost_usd']['mean']:.3f} ({row[s]['cost_usd']['min']:.3f}–{row[s]['cost_usd']['max']:.3f})" for s in sessions]
        chain = row["chain"]
        saving = "–" if variant == "none" or chain.get("saving_vs_none") is None else \
            f"{chain['saving_vs_none']:+.0%}" + (" (noise)" if chain["within_noise"] else "")
        lines.append(f"| {variant} | " + " | ".join(cells) + f" | ${chain['cost_usd']['mean']:.3f} | {saving} | "
                     f"{chain['checks_passed']} | {row.get('s2', {}).get('decisions_applied') or '–'} |")
    lines += ["", "Cost per session: mean (min–max) over repetitions. A saving is marked noise when the spread of "
              "either variant is larger than the difference. S1 states the decisions in its own prompt; S2 is where "
              "they have to come from somewhere.", ""]
    for variant, row in report["summary"].items():
        lines.append(f"- **{variant}**: " + "; ".join(
            f"{s.upper()} {row[s]['turns']['mean']:.0f} turns, {row[s]['seconds']['mean']:.0f} s, "
            f"{row[s]['context_tokens']['mean'] / 1000:.0f}k context tokens, {row[s]['memory_calls']} memory calls, "
            f"{row[s]['judge_calls']} judge calls" for s in sessions))
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", required=True, help="an empty disposable directory outside iCloud")
    parser.add_argument("--variants", nargs="*", default=list(VARIANTS), choices=list(VARIANTS))
    parser.add_argument("--sessions", nargs="*", default=[s[0] for s in SESSIONS], choices=[s[0] for s in SESSIONS])
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--claude", default=shutil.which("claude"))
    parser.add_argument("--model", default="claude-sonnet-5-5")
    parser.add_argument("--max-turns", type=int, default=60)
    parser.add_argument("--jev-command", default=shutil.which("jev") or "jev")
    args = parser.parse_args()
    base = Path(args.workspace).resolve()
    if base.exists() and any(base.iterdir()):
        print(canonical({"error": "Supply an empty disposable workspace."}))
        return 1
    problem = preflight(args.claude, args.model)
    if problem:  # checked before the workspace exists, so the same one can be used again
        print(canonical({"error": "Claude Code cannot make a request: " + problem}))
        return 1
    base.mkdir(parents=True, exist_ok=True)
    home = jev_home(base)
    elsewhere = judge_elsewhere(home, args.jev_command)
    if elsewhere:
        print(canonical({"error": f"jev in {home} sends to {elsewhere}, not this machine; set the judge up with `jev setup`."}))
        return 1
    report = {"client": subprocess.run([args.claude, "--version"], capture_output=True, text=True).stdout.strip(),
              "model": args.model, "sessions": args.sessions, "started": time.strftime("%Y-%m-%d %H:%M"), "runs": []}
    out = base / "report.json"
    rng = random.Random(7)
    for repetition in range(args.repetitions):
        order = list(args.variants)
        rng.shuffle(order)  # interleave so drift in API or local-model latency does not favour one variant
        for variant in order:
            print(f"[{time.strftime('%H:%M:%S')}] rep {repetition + 1} · {variant}", file=sys.stderr, flush=True)
            try:
                run = run_variant(args.claude, base / f"rep{repetition + 1}", variant, args.model, args.jev_command, home,
                                  args.max_turns, args.sessions)
            except Unusable as exc:
                out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
                print(canonical({"error": f"Stopped at rep {repetition + 1} · {variant}: {exc}"}))
                return 1
            run["repetition"] = repetition + 1
            report["runs"].append(run)
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    report["summary"] = summarize(report["runs"], args.sessions)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (base / "REPORT.md").write_text(markdown(report))
    print(markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
