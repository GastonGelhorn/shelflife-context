"""What Shelflife cost the Claude Code sessions of the last days, read from their transcripts, and what its
packets carried, read from the store's traces.

Three costs, each priced at the session's model:
  packets  the packet added to each prompt: written to the prompt cache once, then re-read by every later
           request until the next compaction;
  tools    requests whose only work was a memory tool call: each is a full request, re-reading the context;
  stops    requests made after the Stop hook handed the turn back to save a fact.
Token counts of the packets are estimated from their length; the rest come from the transcripts' usage."""

from collections import Counter
from pathlib import Path
import json
import statistics
import time

# USD per million tokens: input, output, cache read, cache write (5 minutes). List prices, 2026-09-25.
MODEL_PRICES = {
    "claude-fable-5-1": (10.0, 50.0, 0.25, 12.5),
    "claude-fable-5": (10.0, 50.0, 1.0, 12.5),
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-opus-5": (5.0, 25.0, 0.5, 6.25),
    "claude-opus-4": (5.0, 25.0, 0.5, 6.25),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
    "claude-sonnet-5": (2.0, 10.0, 0.2, 2.5),
    "claude-sonnet-4": (3.0, 15.0, 0.3, 3.75),
    "claude-haiku-4": (1.0, 5.0, 0.1, 1.25),
}
DEFAULT_PRICE = (5.0, 25.0, 0.5, 6.25)
CHARS_PER_TOKEN = 3.4  # a packet is JSON with short keys; checked against usage deltas


def price_for(model, prices=None):
    table = {**MODEL_PRICES, **(prices or {})}
    best = max((k for k in table if (model or "").startswith(k)), key=len, default=None)
    return table[best] if best else DEFAULT_PRICE


def _usd(usage, price):
    pi, po, pr, pw = price
    return (usage.get("input_tokens", 0) * pi + usage.get("output_tokens", 0) * po
            + usage.get("cache_read_input_tokens", 0) * pr + usage.get("cache_creation_input_tokens", 0) * pw) / 1e6


def _shelflife(text):
    return "Shelflife" in text or '"type":"context_data"' in text or '"type": "context_data"' in text or "memory_capture" in text


def _entries(path):
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def transcripts(days=7, root=None, prices=None, now=None):
    root = Path(root) if root else Path.home() / ".claude" / "projects"
    cutoff = (now or time.time()) - days * 86400
    total, tools = Counter(), Counter()
    for path in root.glob("*/*.jsonl"):
        if path.stat().st_mtime < cutoff:
            continue
        entries = _entries(path)
        calls, seen = [], set()  # one per API request: (index, model, usage, tool names)
        for i, e in enumerate(entries):
            m = e.get("message") or {}
            if e.get("type") != "assistant" or not m.get("usage") or e.get("isSidechain"):
                continue
            names = [c.get("name") or "" for c in m.get("content") or [] if isinstance(c, dict) and c.get("type") == "tool_use"]
            if m.get("id") in seen:
                calls[-1][3].extend(names)
                continue
            seen.add(m.get("id"))
            calls.append((i, m.get("model") or "", m["usage"], names))
        if not calls:
            continue
        model = Counter(c[1] for c in calls).most_common(1)[0][0]
        price = price_for(model, prices)
        compactions = [i for i, e in enumerate(entries) if e.get("type") == "system" and e.get("subtype") == "compact_boundary"]
        session_usd = sum(_usd(c[2], price_for(c[1], prices)) for c in calls)
        shelf = Counter()
        for i, e in enumerate(entries):
            a = e.get("attachment") or {}
            hook = a.get("hookName") or a.get("hookEvent")
            if a.get("type") == "hook_additional_context" and hook == "UserPromptSubmit":
                body = json.dumps(a.get("content"), ensure_ascii=False)
                if not _shelflife(body):
                    continue
                tokens = len(body) / CHARS_PER_TOKEN
                until = min([j for j in compactions if j > i] or [len(entries)])
                rereads = sum(1 for c in calls if i < c[0] < until)
                shelf["packets"] += 1
                shelf["packet_tokens"] += tokens
                shelf["packet_usd"] += tokens * (price[3] + rereads * price[2]) / 1e6
            if a.get("type") == "hook_blocking_error" and (a.get("hookEvent") or a.get("hookName")) == "Stop" \
                    and _shelflife(json.dumps(a, ensure_ascii=False)):
                shelf["stops"] += 1
                typed = next((j for j, x in enumerate(entries) if j > i and x.get("type") == "user" and not x.get("toolUseResult")
                              and isinstance((x.get("message") or {}).get("content"), str)), len(entries))
                extra = [c for c in calls if i < c[0] < typed]
                shelf["stop_requests"] += len(extra)
                shelf["stop_usd"] += sum(_usd(c[2], price_for(c[1], prices)) for c in extra)
        for c in calls:
            if c[3] and all("shelflife" in n for n in c[3]):
                shelf["tool_requests"] += 1
                shelf["tool_usd"] += _usd(c[2], price_for(c[1], prices))
                tools.update(n.rsplit("__", 1)[-1] for n in c[3])
        total["sessions"] += 1
        total["requests"] += len(calls)
        total["session_usd"] += session_usd
        if shelf:
            total["sessions_with_shelflife"] += 1
            total["their_usd"] += session_usd
            total.update(shelf)
    cost = total["packet_usd"] + total["tool_usd"] + total["stop_usd"]
    money = lambda v: round(v, 2)  # noqa: E731
    return {
        "days": days, "sessions": total["sessions"], "requests": total["requests"], "sessions_usd": money(total["session_usd"]),
        "sessions_with_shelflife": total["sessions_with_shelflife"],
        "packets": {"count": total["packets"], "tokens_each": round(total["packet_tokens"] / max(1, total["packets"])),
                    "usd": money(total["packet_usd"])},
        "tools": {"requests": total["tool_requests"], "usd": money(total["tool_usd"]), "calls": dict(tools.most_common())},
        "stops": {"handed_back": total["stops"], "requests": total["stop_requests"], "usd": money(total["stop_usd"])},
        "shelflife_usd": money(cost),
        "share_of_their_sessions": round(cost / total["their_usd"], 4) if total["their_usd"] else 0.0,
    }


def packets(store, limit=100):
    """What the last packets carried and what selecting them took, from the store's traces."""
    traces = store.traces(limit)
    if not traces:
        return {"packets": 0}
    claims = [len(t.get("selected") or []) for t in traces]
    warned = lambda name: round(sum(1 for t in traces if name in (t.get("warnings") or [])) / len(traces), 3)  # noqa: E731
    judged = [(t.get("usage") or {}).get("judged_pairs", 0) for t in traces]
    times = sorted(t.get("latency_ms") or 0 for t in traces)
    return {"packets": len(traces), "with_claims": round(sum(1 for c in claims if c) / len(traces), 3),
            "claims_each": round(statistics.mean(claims), 2), "judged_pairs_each": round(statistics.mean(judged), 1),
            "judge_unavailable": warned("jev_unavailable"), "no_plan": warned("unknown_task"),
            "median_ms": round(statistics.median(times)), "p90_ms": round(times[min(len(times) - 1, int(0.9 * len(times)))])}


def report(store, days=7, root=None, prices=None):
    return {"cost": transcripts(days, root, prices), "selection": packets(store) if store is not None else None}
