"""Opt-in cross-agent check on an example project: Claude Code is taught, then Codex carries on.

`--phase claude` builds a small example repository per variant and repetition (a checkout service
with one accepted decision record) and runs the teaching sessions of tests.compare_check in fresh
headless Claude Code sessions: facts stated, corrected, changed and forgotten, recommendations made,
the decision record superseded. It keeps a copy of the memory as it stood, then asks the checks in
Claude Code. `--phase codex` asks the same checks in fresh headless Codex sessions on the same
repositories, each from its own copy of the memory as Claude Code left it, so neither agent's
answers reach the other's. Codex runs project hooks only once a person has trusted them in that
folder, so headless Codex gets memory the way an agent without hooks does: the repository's
decision records learned, the brief in AGENTS.md, and the hookless MCP server. `--phase report` writes REPORT.md. Everything lives in the workspace
(state.json), so the phases can run apart, from either agent, and a phase resumes where it stopped.

Variants: none (the agent has only the repository) and shelflife (the kernel's hooks and MCP server,
jev selection). jevmate's plugin is off in both. Prompts are fictional; the Claude phase uses the
owner's Claude Code sign-in, the Codex phase the owner's ChatGPT sign-in, never an API key.
"""

import argparse
import json
import random
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
from pathlib import Path

from shelflife_context.adapters import configuration
from shelflife_context.store import Store
from tests.compare_check import OTHER_PLUGINS, READ_TOOLS, SCRIPT, invoke as claude_invoke, kernel_summary, passes, timed
from tests.native_claude_check import MEMORY_TOOLS
from tests.native_codex_check import child_environment
from tests.native_v06_check import git, record, wait_for_pass


VARIANTS = ("none", "shelflife")
TEACH = [step for step in SCRIPT if step[3] is None]
CHECKS = [step for step in SCRIPT if step[3] is not None]
CODEX_APP = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"
ROOT = Path(__file__).resolve().parent.parent

# None of the facts the script teaches is in the repository: they exist only in what was said.
EXAMPLE = {
    "README.md": "# checkout\n\nThe checkout service: cart totals, payments and the order emails.\n\n"
                 "- `src/cart.js` totals a cart\n- `src/payments.js` charges a card through the payment gateway\n"
                 "- `src/email-worker.js` sends the order emails from the job queue\n\n"
                 "Decisions are recorded in `docs/adr`.\n",
    "package.json": json.dumps({"name": "checkout", "version": "0.4.0", "private": True,
                                "scripts": {"start": "node src/server.js", "test": "node --test"}}, indent=2) + "\n",
    "src/cart.js": "export function total(items) {\n  return items.reduce((sum, item) => sum + item.price * item.quantity, 0)\n}\n",
    "src/payments.js": "// Charges a card through the payment gateway. Written in a hurry for the first release.\n"
                       "export async function charge(gateway, order) {\n"
                       "  const response = await gateway.post('/charges', { amount: order.total, token: order.cardToken })\n"
                       "  if (!response.ok) throw new Error('charge failed')\n  return response.id\n}\n",
    "src/email-worker.js": "// Sends the order emails. Jobs come from the queue the project chose (docs/adr).\n"
                           "export async function run(queue, mailer) {\n"
                           "  for await (const job of queue.jobs('order-email')) await mailer.send(job.to, job.body)\n}\n",
    "src/server.js": "import { total } from './cart.js'\n\nconsole.log('checkout listening', total([]))\n",
}


def example(root):
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    for name, text in EXAMPLE.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    record(root, 1, "Use SQLite for the job queue", "Accepted", "Jobs stay in one SQLite file next to the app.")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "The checkout service and its queue decision")


def supersede(root):
    record(root, 1, "Use SQLite for the job queue",
           "Superseded by [2. Use Redis for the job queue](0002-use-redis-for-the-job-queue.md)",
           "Jobs stay in one SQLite file next to the app.")
    record(root, 2, "Use Redis for the job queue", "Accepted", "Jobs move to Redis so several workers can share them.")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "Move the job queue to Redis")


def copy_db(source, target):
    """SQLite's backup API: a consistent copy, the write-ahead log included."""
    src, dst = sqlite3.connect(source), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()


def signature_of(database, root):
    store = Store(database, scope="pilot")
    try:
        row = store.repository(str(Path(root).resolve()))
        return row["signature"] if row else None
    finally:
        store.close()


# ---------------------------------------------------------------- Claude Code

def wire_claude(root, database, hook_log, jev_command):
    settings = {"enabledPlugins": {name: False for name in OTHER_PLUGINS + ("jevmate@gastongelhorn",)}}
    mcp = {"mcpServers": {}}
    if database:
        hooks = configuration("claude", root, database, "pilot", strategy="jev", jev_command=jev_command)["config"]["hooks"]
        for event, groups in hooks.items():
            for group in groups:
                for hook in group["hooks"]:
                    hook["command"] = timed(hook["command"], hook_log, event)
        settings["hooks"] = hooks
        mcp = configuration("claude", root, database, "pilot", mode="mcp", strategy="jev", jev_command=jev_command)["config"]
    (root / ".claude").mkdir(exist_ok=True)
    (root / ".claude" / "settings.local.json").write_text(json.dumps(settings, indent=2))
    mcp_path = root.parent / (root.name + "-mcp.json")
    mcp_path.write_text(json.dumps(mcp, indent=2))
    return mcp_path, ",".join([READ_TOOLS] + ([MEMORY_TOOLS] if database else []))


def claude_phase(run, claude, model, jev_command):
    root = Path(run["root"])
    database = Path(run["teach_db"]) if run["variant"] == "shelflife" else None
    if "teach" not in run:
        example(root)
        if database:
            Store(database, scope="pilot", create=True).close()
        mcp_path, allowed = wire_claude(root, database, Path(run["hook_log"]), jev_command)
        steps = []
        for index, (task, kind, prompt, _) in enumerate(TEACH):
            if kind == "repo_change":
                supersede(root)
                continue
            result = claude_invoke(claude, root, prompt, mcp_path, allowed, model)
            result.update(task=task, kind=kind, agent="claude")
            if database and index == 0:
                wait_for_pass(database, root, seconds=60)  # the first session starts the repository pass
            steps.append(result)
        run["teach"] = steps
        if database:
            run["teach_signature"] = signature_of(database, root)
            run["kernel_after_teach"] = kernel_summary(database)
    run.setdefault("checks", {})["claude"] = ask(run, "claude", lambda root, prompt, db: claude_invoke(
        claude, root, prompt, *wire_claude(root, db, Path(run["hook_log"]), jev_command), model))


# ---------------------------------------------------------------- Codex

def codex_invoke(codex, root, prompt, database, jev_command, model):
    command = [codex, "exec", "--ignore-user-config", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only",
               "--disable", "apps", "--disable", "multi_agent", "--config", 'web_search="disabled"',
               "--config", 'model_provider="openai"', "--config", 'forced_login_method="chatgpt"', "--json", "--cd", str(root)]
    if model:
        command += ["--model", model]
    if database:
        server = configuration("generic", root, database, "pilot", strategy="jev",
                               jev_command=jev_command)["config"]["mcpServers"]["shelflife-context"]
        command += ["--config", "mcp_servers.shelflife-context.command=" + json.dumps(server["command"]),
                    "--config", "mcp_servers.shelflife-context.args=" + json.dumps(server["args"]),
                    "--config", "mcp_servers.shelflife-context.env={PYTHONPATH = " + json.dumps(server["env"]["PYTHONPATH"]) + "}"]
    command.append(prompt)
    started = time.perf_counter()
    try:
        done = subprocess.run(command, input="", text=True, capture_output=True, timeout=240, env=child_environment())
    except subprocess.TimeoutExpired:
        return {"exit_code": 124, "answer": None, "seconds": 240.0}
    out = {"exit_code": done.returncode, "seconds": round(time.perf_counter() - started, 3), "tools": [], "hooks": []}
    for line in done.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item") if event.get("type") == "item.completed" else None
        if isinstance(item, dict):
            if item.get("type") == "agent_message":
                out["answer"] = item.get("text")
            elif item.get("type") == "mcp_tool_call":
                out["tools"].append(f"mcp__{item.get('server')}__{item.get('tool')}")
            elif item.get("type") == "command_execution":
                out["tools"].append("shell")
        elif event.get("type") == "turn.completed":
            usage = event.get("usage") or {}
            out["input_tokens"] = (out.get("input_tokens") or 0) + (usage.get("input_tokens") or 0)
            out["output_tokens"] = (out.get("output_tokens") or 0) + (usage.get("output_tokens") or 0)
    if done.returncode:
        out["stderr"] = done.stderr[-800:]
    return out


def kernel(database, *args):
    command = [sys.executable, "-m", "shelflife_context", "--db", str(database), "--scope", "pilot", *args]
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=300)
    return {"command": args[0], "exit_code": done.returncode, "out": (done.stdout + done.stderr)[-600:]}


def codex_phase(run, codex, model, jev_command):
    def before(root, database):
        # What a hook-less agent has: the decision records learned and the brief written to AGENTS.md.
        run["codex_setup"] = [kernel(database, "learn", "--workspace", str(root), "--strategy", "jev", "--jev-command", jev_command),
                              kernel(database, "brief", "--workspace", str(root), "--on")]
    run.setdefault("checks", {})["codex"] = ask(run, "codex", lambda root, prompt, db: codex_invoke(
        codex, root, prompt, db, jev_command, model), before=before)


# ---------------------------------------------------------------- both

def ask(run, agent, invoke, before=None):
    """The checks in one agent, from its own copy of the memory as the teaching left it."""
    root = Path(run["root"])
    database = None
    if run["variant"] == "shelflife":
        database = root.parent / f"{root.name}-memory-{agent}.sqlite"
        copy_db(run["teach_db"], database)
        if before:
            before(root, database)
    steps = []
    for index, (task, kind, prompt, check) in enumerate(CHECKS):
        result = invoke(root, prompt, database)
        result.update(task=task, kind=kind, agent=agent, passed=passes(check, result.get("answer")))
        if database and index == 0 and not before:
            # The decision record changed after the teaching: this session started the pass that reads it.
            wait_for_pass(database, root, previous=run.get("teach_signature"), seconds=60)
        steps.append(result)
    if database:
        run.setdefault("kernel_after", {})[agent] = kernel_summary(database)
    return steps


def summarize(state):
    rows = []
    for agent in ("claude", "codex"):
        for variant in state["variants"]:
            steps = [s for r in state["runs"] if r["variant"] == variant for s in r.get("checks", {}).get(agent, [])]
            if not steps:
                continue
            count = lambda kinds, ok=True: sum(s["passed"] == ok for s in steps if s["kind"] in kinds)
            total = lambda kinds: sum(s["kind"] in kinds for s in steps)
            rows.append({
                "agent": agent, "variant": variant, "runs": len({id(r) for r in state["runs"] if r["variant"] == variant and agent in r.get("checks", {})}),
                "recall": f"{count({'recall'})}/{total({'recall'})}", "stale_flagged": f"{count({'stale'})}/{total({'stale'})}",
                "forgotten_kept_out": f"{count({'forgotten'})}/{total({'forgotten'})}", "generic_ok": f"{count({'generic'})}/{total({'generic'})}",
                "all_checks": f"{sum(s['passed'] for s in steps)}/{len(steps)}",
                "median_s": round(statistics.median(s["seconds"] for s in steps), 1),
                "input_tokens": sum(s.get("input_tokens") or 0 for s in steps),
                "cost_usd": round(sum(s.get("cost_usd") or 0 for s in steps), 3) if agent == "claude" else None,
                "memory_calls": sum(sum("shelflife-context" in t for t in s.get("tools", [])) for s in steps),
                "errors": sum(bool(s.get("exit_code")) for s in steps),
            })
    return rows


def write_report(workspace, state):
    rows = summarize(state)
    state["summary"] = rows
    head = ("| agent | memory | recall | stale advice flagged | forgotten kept out | generic | all checks | median s | "
            "input tokens | cost USD | memory calls | errors |\n|" + " --- |" * 12)
    lines = [f"| {r['agent']} | {r['variant']} | {r['recall']} | {r['stale_flagged']} | {r['forgotten_kept_out']} | {r['generic_ok']} | "
             f"{r['all_checks']} | {r['median_s']} | {r['input_tokens']:,} | {r['cost_usd'] if r['cost_usd'] is not None else '–'} | "
             f"{r['memory_calls']} | {r['errors']} |" for r in rows]
    failed = [f"- {s['agent']} · {r['variant']} · rep {r['repetition']} · {s['task']}/{s['kind']}: "
              f"{(s.get('answer') or s.get('stderr') or '(no answer)')[:220]!r}"
              for r in state["runs"] for agent in ("claude", "codex") for s in r.get("checks", {}).get(agent, []) if not s["passed"]]
    text = (f"# Cross-agent check\n\nClaude Code {state['clients'].get('claude', '?')}; Codex {state['clients'].get('codex', 'not run yet')}. "
            f"{len(state['runs'])} runs: {', '.join(state['variants'])} × {state['repetitions']} repetitions. "
            "Taught in Claude Code; each agent then answers the checks from its own copy of the memory.\n\n"
            + head + "\n" + "\n".join(lines) + "\n\n## Checks that failed\n\n" + ("\n".join(failed) or "None.") + "\n")
    (workspace / "REPORT.md").write_text(text, encoding="utf-8")
    return text


def save(workspace, state):
    (workspace / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", required=True, help="a disposable directory outside iCloud")
    parser.add_argument("--phase", required=True, choices=("claude", "codex", "report"))
    parser.add_argument("--variants", nargs="*", default=list(VARIANTS), choices=VARIANTS)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--claude", default=shutil.which("claude"))
    parser.add_argument("--codex", default=shutil.which("codex") or CODEX_APP)
    parser.add_argument("--model", help="Claude Code's model (its default if omitted)")
    parser.add_argument("--codex-model", help="Codex's model (its default if omitted)")
    parser.add_argument("--jev-command", default=shutil.which("jev") or "jev")
    args = parser.parse_args()
    workspace = Path(args.workspace).expanduser().resolve()
    state_path = workspace / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
    elif args.phase != "claude":
        raise SystemExit(f"No state in {workspace}: run --phase claude first.")
    else:
        if workspace.exists() and any(workspace.iterdir()):
            raise SystemExit("Supply an empty disposable workspace.")
        workspace.mkdir(parents=True, exist_ok=True)
        state = {"started": time.strftime("%Y-%m-%d %H:%M"), "variants": args.variants, "repetitions": args.repetitions,
                 "clients": {}, "runs": []}
        order = []
        rng = random.Random(7)
        for repetition in range(1, args.repetitions + 1):
            variants = list(args.variants)
            rng.shuffle(variants)  # interleaved, so drift in latency does not favour one variant
            order += [(repetition, v) for v in variants]
        for repetition, variant in order:
            name = workspace / f"rep{repetition}" / variant
            state["runs"].append({"repetition": repetition, "variant": variant, "root": str(name),
                                  "teach_db": str(name.parent / f"{variant}-memory-teach.sqlite"),
                                  "hook_log": str(name.parent / f"{variant}-hooks.jsonl")})
        save(workspace, state)
    if args.phase == "claude":
        state["clients"]["claude"] = subprocess.run([args.claude, "--version"], capture_output=True, text=True).stdout.strip()
    elif args.phase == "codex":
        state["clients"]["codex"] = subprocess.run([args.codex, "--version"], capture_output=True, text=True).stdout.strip()
    for run in state["runs"] if args.phase != "report" else []:
        if args.phase in run.get("checks", {}):
            continue  # done in an earlier invocation: resume where it stopped
        if args.phase == "codex" and "claude" not in run.get("checks", {}) and "teach" not in run:
            continue
        print(f"[{time.strftime('%H:%M:%S')}] {args.phase} · rep {run['repetition']} · {run['variant']}", file=sys.stderr, flush=True)
        (claude_phase if args.phase == "claude" else codex_phase)(
            run, args.claude if args.phase == "claude" else args.codex,
            args.model if args.phase == "claude" else args.codex_model, args.jev_command)
        save(workspace, state)
        write_report(workspace, state)
    print(write_report(workspace, state))
    save(workspace, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
