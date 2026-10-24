"""Real prompts through the prompt hook, against a copy of a memory store: what each packet carries, what it
cost in judge calls and time, and, with labels, how much of it was relevant.

  python3 -m tests.packet_replay extract --days 7 --sample 150 --out DIR/prompts.jsonl
  python3 -m tests.packet_replay run --prompts DIR/prompts.jsonl --db DIR/memory.sqlite --out DIR/before.jsonl [--strategy rules]
  python3 -m tests.packet_replay score DIR/before.jsonl [--labels DIR/labels.jsonl]

The prompts are the owner's own words and the store holds their memory: keep both, and the labels, outside
this repository. `run` refuses the live store; copy it first. The copy's turns are cleared, so what a session
already received is only what the replay sent it. jev writes its ledger to a home of its own (`DIR/jev-home`),
so the owner's figures stay untouched. Labels are lines of {"prompt": ID, "claim": ID, "relevant": true|false};
standing facts go to every session by design and are left out of precision."""

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import sqlite3
import time
from collections import Counter
from pathlib import Path

from shelflife_context.turns import typed_text

HOME = Path.home()
LIVE = HOME / ".shelflife-context" / "memory.sqlite"
PACKET = re.compile(r'\{"as_of".*\}', re.S)


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list) and not any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def extract(days, sample, out, seed=7):
    """Every prompt Claude Code sent to a main conversation in the last `days`, sampled, in time order."""
    cutoff = time.time() - days * 86400
    prompts = []
    for path in HOME.glob(".claude/projects/*/*.jsonl"):
        if path.stat().st_mtime < cutoff:
            continue
        for line in path.open(encoding="utf-8", errors="replace"):
            if '"type":"user"' not in line and '"type": "user"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") != "user" or e.get("isSidechain") or e.get("isMeta") or e.get("toolUseResult") is not None:
                continue
            text = _text((e.get("message") or {}).get("content"))
            stamp = e.get("timestamp") or ""
            if not text.strip() or not e.get("cwd") or not e.get("sessionId"):
                continue
            if time.mktime(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")) < cutoff - 86400:
                continue
            pid = hashlib.sha256(f"{e['sessionId']}|{stamp}".encode()).hexdigest()[:12]
            prompts.append({"id": pid, "session": e["sessionId"], "cwd": e["cwd"], "ts": stamp, "prompt": text})
    random.Random(seed).shuffle(prompts)
    chosen = sorted(prompts[:sample], key=lambda p: p["ts"])
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for p in chosen:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(f"{len(chosen)} of {len(prompts)} prompts → {out}")


def _jev_home(base):
    home = Path(base) / "jev-home"
    if not home.exists():
        home.mkdir(parents=True)
        for real in (os.environ.get("JEV_HOME"), HOME / ".config" / "jev", HOME / ".config" / "typesafe"):
            if real and (Path(real) / "config.json").exists():
                shutil.copy(Path(real) / "config.json", home / "config.json")
                break
    return home


def _ledger_rows(home):
    try:
        return (home / "usage.jsonl").read_text().splitlines()
    except OSError:
        return []


def run(prompts, db, out, scope="work", jev=None, strategy="jev"):
    db = Path(db).expanduser().resolve()
    if db == LIVE.resolve():
        raise SystemExit("run needs a copy of the store, never the live one")
    from shelflife_context.plugin import find_jev
    jev = (jev or find_jev()) if strategy == "jev" else None
    home = _jev_home(db.parent)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_PLUGIN_OPTION_", "JEV_SESSION", "JEV_AGENT"))}
    env.update(JEV_HOME=str(home), PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    env.pop("SHELFLIFE_CONTEXT_OFF", None)
    rows = [json.loads(l) for l in open(prompts, encoding="utf-8")]
    with sqlite3.connect(db) as copy:  # the real sessions' turns, later ones included, would count as delivered
        copy.execute("DELETE FROM turns")
    with open(out, "w", encoding="utf-8") as f:
        for n, p in enumerate(rows, 1):
            event = {"hook_event_name": "UserPromptSubmit", "session_id": p["session"], "cwd": p["cwd"], "prompt": p["prompt"]}
            command = [sys.executable, "-m", "shelflife_context", "--db", str(db), "--scope", scope, "hook", "--client", "claude",
                       "--workspace", p["cwd"], "--event", "prompt", "--strategy", "jev" if jev else "rules"] + (["--jev-command", jev] if jev else [])
            before = len(_ledger_rows(home))
            started = time.perf_counter()
            done = subprocess.run(command, input=json.dumps(event), capture_output=True, text=True, env=env, timeout=60)
            ms = round((time.perf_counter() - started) * 1000)
            calls = [json.loads(l) for l in _ledger_rows(home)[before:]]
            result = {"id": p["id"], "session": p["session"], "cwd": p["cwd"], "ms": ms, "judge_calls": len(calls), "judge_ms": sum(c.get("ms", 0) for c in calls),
                      "prompt": p["prompt"][:200], "claims": [], "warnings": [], "asks": [], "error": None}
            try:
                context = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
                found = PACKET.search(context)
                packet = json.loads(found.group(0)) if found else {}
                result["claims"] = [{"id": c["id"], "entity": c.get("entity"), "predicate": c.get("predicate"),
                                     "value": str(c.get("value"))[:160], "standing": bool(c.get("standing"))} for c in packet.get("claims", [])]
                result["warnings"] = packet.get("warnings", [])
                result["asks"] = [a.strip()[:80] for a in context[:found.start() if found else len(context)].split("asks:")[1:]]
                result["context_chars"] = len(context)
            except (ValueError, KeyError, TypeError):
                result["error"] = (done.stderr or done.stdout).strip()[-300:]
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
            f.flush()
            print(f"{n}/{len(rows)} {ms} ms · {len(result['claims'])} claims · {len(calls)} judge calls", file=sys.stderr)


def score(result, labels=None, prompts=None):
    rows = [json.loads(l) for l in open(result, encoding="utf-8")]
    full = {p["id"]: p for p in map(json.loads, open(prompts, encoding="utf-8"))} if prompts else {}
    for r in rows:  # results keep 200 characters of each prompt, which can end before the typed part
        r.setdefault("session", full.get(r["id"], {}).get("session"))
        r["typed"] = typed_text(full.get(r["id"], r)["prompt"])
    marks = {}
    if labels:
        for l in open(labels, encoding="utf-8"):
            if l.strip():
                m = json.loads(l)
                marks[(m["prompt"], m["claim"])] = bool(m["relevant"])
    ok = [r for r in rows if not r["error"]]
    claims = [len(r["claims"]) for r in ok]
    ms = sorted(r["ms"] for r in ok)
    others = [r for r in ok if not r["typed"]]
    report = {
        "prompts": len(rows), "errors": len(rows) - len(ok),
        "with_claims": round(sum(1 for c in claims if c) / max(1, len(ok)), 3),
        "claims_per_packet": round(statistics.mean(claims), 2) if claims else 0,
        "context_chars": round(statistics.mean(r.get("context_chars", 0) for r in ok)) if ok else 0,
        "not_typed": len(others), "not_typed_with_claims": sum(1 for r in others if any(not c["standing"] for c in r["claims"])),
        "ms_p50": ms[len(ms) // 2] if ms else None, "ms_p90": ms[int(len(ms) * 0.9)] if ms else None,
        "judge_calls_per_prompt": round(statistics.mean(r["judge_calls"] for r in ok), 2) if ok else 0,
        "asks": sum(len(r["asks"]) for r in ok),
        "warnings": dict(Counter(w for r in ok for w in r["warnings"]).most_common()),
    }
    if marks:
        delivered = [(r["id"], c["id"]) for r in ok for c in r["claims"] if not c["standing"]]
        judged = [d for d in delivered if d in marks]
        relevant = sum(1 for d in judged if marks[d])
        known = {k for k, v in marks.items() if v}
        # A claim the session got at an earlier prompt is still in its context: covered, not missed.
        held, covered = {}, 0
        for r in rows:
            seen = held.setdefault(r.get("session") or r["id"], set())
            seen |= {c["id"] for c in r["claims"]}
            covered += sum(1 for (p, c) in known if p == r["id"] and c in seen)
        report.update({"labelled": len(judged), "unlabelled": len(delivered) - len(judged),
                       "precision": round(relevant / max(1, len(judged)), 3),
                       "relevant_kept": f"{relevant} of {len(known)}",
                       "relevant_in_context": f"{covered} of {len(known)}"})
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--days", type=float, default=7)
    e.add_argument("--sample", type=int, default=150)
    e.add_argument("--out", required=True)
    r = sub.add_parser("run")
    r.add_argument("--prompts", required=True)
    r.add_argument("--db", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--scope", default="work")
    r.add_argument("--jev")
    r.add_argument("--strategy", choices=("jev", "rules"), default="jev", help="rules: no judge at all")
    s = sub.add_parser("score")
    s.add_argument("result")
    s.add_argument("--labels")
    s.add_argument("--prompts", help="the prompts file, for results written before rows carried their session")
    args = parser.parse_args(argv)
    if args.command == "extract":
        extract(args.days, args.sample, args.out)
    elif args.command == "run":
        run(args.prompts, args.db, args.out, args.scope, args.jev, args.strategy)
    else:
        score(args.result, args.labels, args.prompts)


if __name__ == "__main__":
    main()
