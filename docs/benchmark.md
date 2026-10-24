# Measuring it yourself

Every number in the README comes from one of these scripts. They need a checkout and `jev` (local by default); none of them needs a Claude or Codex account except the native checks at the end.

| Script | What it measures | Needs |
| --- | --- | --- |
| `python3 -m tests.bench` | 30 multi-session scenarios replayed through the kernel's own hooks and MCP server | jev |
| `python3 -m tests.bench --filler 0 100 500 1000` | the same scenarios among more and more unrelated facts | jev |
| `python3 -m tests.bench --replay fixtures/bench/recorded.json` | the same, on answers recorded from the local judge: deterministic, runs in CI | nothing |
| `python3 -m tests.relevance_check` | selection on 60 labelled questions over 122 facts | jev (or `--scores fixtures/relevance_scores.json`) |
| `python3 -m tests.holdout_check` | the whole capture path on 38 held-out messages | jev |
| `python3 -m tests.growth_check` | an old constraint asked about in other words, as memory grows | jev |
| `python3 -m tests.packet_replay` | your own recent prompts through the prompt hook, against a copy of your store: claims per packet, judge calls, hook time and, with labels, precision | jev, your transcripts |
| `python3 -m tests.savings_check` | cost and correctness of three chained Claude Code sessions on a fixture service, with no plugin, jevmate, Shelflife and both | Claude Code, jev |
| `python3 -m tests.compat_matrix` | end to end in the real clients, with their versions recorded | Claude Code (and a trusted Codex pilot) |

The latest results, and the failures behind them, are in [verification](verification.md#the-benchmark). In short, with the local judge and the hook's real budget: 193 of 211 checks with an empty memory, 186 with 200 unrelated facts, 179 with 1,000.

## The scenarios (`fixtures/bench`)

Thirty JSON files, half in English, half in Spanish, four mixed. Each one is two to four sessions of a few turns: what the user types, what a competent agent proposes to save (with the user's words as `quote`), the other memory tools it calls, its reply, and what should be true afterwards. They cover:

- facts stated in passing (deadlines, approvers, budgets, decisions, preferences);
- messages a sloppy agent would save but the kernel must not: questions, hypotheticals, sarcasm, requests for work, pasted mail and logs, someone else's private life, past states, uncertainty;
- corrections under a different key name, ambiguous changes ("the deadline moved") that must not replace anything;
- recommendations resting on one or several facts, flagged for review when one changes and not when something unrelated does;
- forgetting one property, forgetting a whole thing, undoing the last save;
- rules that must reach every later session;
- decision records accepted, superseded, forgotten, and routine commits that must not become facts;
- "don't remember this".

The agent's part is fixed, so the benchmark measures the kernel and its judge, not an agent's extraction. The cues the agent passes are in `fixtures/bench/cues.json`; Claude Sonnet wrote them from each message and fact alone, never seeing a later question. `fixtures/bench/filler.json` holds 1,000 unrelated facts with cues written the same way.

Each expectation is one check. The report gives the share passed per kind of check (delivered, not delivered, review flagged or not, current value, absent, held, standing) and per scenario tag, plus hook latencies. Prompt hooks run with the production budget of 8 seconds unless `--budget none`.

## The relevance set (`fixtures/relevance.json`)

Four workspaces (a SaaS engineer, a freelance mobile developer with three clients, a platform engineer, a person mixing work and personal context), 26 to 34 facts each, and 60 questions: 33 paraphrased (no word in common with the facts that matter), 15 direct, 12 generic (nothing stored should come). Nine questions need two or three facts together. Labels say which facts a careful colleague answering would take into account; a fact merely on the same topic is not one. Cues (`fixtures/relevance_cues.json`) were written blind to the questions.

`saas` and `freelance` are where the selection rule was chosen. `platform` and `personal` are held out and reported separately.

## Held-out capture (`fixtures/holdout.jsonl`)

38 messages written after the capture thresholds were set, never used to tune them: 18 that state a fact and 20 that do not (questions, a sister's diet, sarcasm, a pasted ticket, a request for work, a log line…). Each goes through the real prompt hook and `memory_capture`, as if the agent proposed exactly the row's fact.

## Recording and replaying the judge

`python3 -m tests.bench --record FILE` runs with the real judge and keeps every answer; `--replay FILE` answers from it. A replay is deterministic and needs no model, so CI runs the benchmark on every push and fails when the share of checks passed drops (`--min-rate`). A request the recording lacks (the kernel now asks something different) counts as the judge not answering and shows up as `replay_missing`. Recordings run without the time limit, so what gets judged does not depend on the machine.

## Real prompts (`tests.packet_replay`)

The scenarios above are written for the purpose. The replay takes the prompts a person actually sent: 150
sampled from one week of one developer's Claude Code sessions (29 sessions in 13 folders, Spanish and English,
including the text Claude Code adds itself: task notifications, system reminders, continuation summaries), each
sent through the prompt hook with its folder, against a copy of that person's store (about 140 facts) and the
local judge. 546 prompt and fact pairs were labelled by hand, asking whether a careful colleague answering the
prompt would want that fact; 91 were relevant. Each version is replayed from a pinned commit on a fresh copy of
the store whose turns are cleared, so what a session already received is only what the replay sent it.

| | v0.10.1 | v0.11 | v0.11, judge not loaded |
| --- | --- | --- | --- |
| Packets carrying claims | 81% | 60% | 61% |
| Claims per packet | 2.17 | 1.45 | 1.05 |
| Packet size, characters | 2,021 | 1,480 | 809 |
| Prompts with no typed text that got claims (of 11) | 10 | 0 | 0 |
| Relevant facts in the session's context (of 91) | 67 | 80 | 62 |
| Delivered claims that were relevant | 16% | 21% | 29% |
| Judge calls per prompt | 24.4 | 8.1 | 0 |
| Prompt hook, median / p90 | 7.4 s / 8.1 s | 3.0 s / 3.7 s | 0.2 s / 0.2 s |

"In the session's context" counts a relevant fact delivered at that prompt or at an earlier one in the same
session. What v0.11 changes:

- Text nobody typed (a task notification, a system reminder, a continuation summary, a local command's output)
  gets the turn token and standing facts, nothing selected. Typed words after such a block are still read.
- A fact delivered earlier in the session is not sent again until the session restarts or compacts. A changed
  premise or a review still brings it.
- When the judge has no plan, a keyword match counts only on what a fact is about (its entity and property),
  two facts at most. Before, every full-text match went in.
- The judge gets the pairs that fit in 2.5 seconds, the likely ones first.

Two changes were measured and left out:

- **Higher bars.** On the labelled pairs the judge's score separates relevant from not only weakly: at 0.35 a
  fact is relevant 23% of the time (65 of 71 relevant ones kept), at 0.5 28% (51), at 0.65 42% (34), at 0.8
  64% (9). A fact that is not needed costs about $0.004 in a session (written to the prompt cache once,
  re-read by later requests); one that is needed can save a request, about $0.24 on the same week's average.
  The bars stay where they are.
- **Scoping by project.** 23 of the 91 relevant facts were saved in a session that ran in another folder: people
  open a session wherever they are and ask about anything. In the v0.11 replay, facts from other folders were
  94 deliveries, 11 of them relevant; asking those for a score of 0.65 would drop 63 of the 83 that were not
  needed and 3 of the 11 that were. At the prices above that does not pay, so a fact's folder plays no part in
  selection.

## What memory costs in a week (`memory report`)

`memory --pretty report --days 7` reads the Claude Code transcripts of the last days and prices three things
at each session's model: the packets (written to the prompt cache once, then re-read by every later request
until the next compaction), the requests whose only work was a memory tool call, and the requests made after
the Stop hook handed a turn back to save a fact. On the same week as the replay, with v0.10.1, the three came
to 0.8% of those sessions' cost: 34% packets, 29% memory-only requests (71 of 72 were `memory_capture`) and 37%
hand-backs. v0.11 hands nothing back by default (`memory policy --stop-nudge on` restores it) and asks for a
capture in the same message as the turn's first other tool call, which adds no request.

## Real sessions (`tests.savings_check`)

The replay measures what a packet carries; it cannot say whether a session then costs less. The savings bench
runs real headless Claude Code sessions on a fixture: `fixtures/savings-repo`, a small invoicing service with a
test suite, three decision records and a planted rounding bug. Each run is a fresh git repository and three
sessions in a row:

1. The owner states three decisions (new features go behind a flag, changes write an audit entry, an
   overpayment is rejected) and asks for credit notes, with tests.
2. "Add partial payments the way we agreed": the decisions are needed and nobody restates them. The overpayment
   rule exists only in what the owner said in session 1.
3. A customer reports a total one cent lower than the sum of its lines; find the cause, fix it, add a test.

The variants are no plugin, jevmate, Shelflife (this checkout's kernel, judged by jev) and both. Claude Code's
own auto memory is off in all of them. After each session the harness commits the work and
`tests/savings_probe.py` checks it: the suite passes, the feature works, each decision was applied, and in
session 3 the new tests fail on the code as it was before and the answer names the cause. The report gives each
variant's cost per session and per chain (mean and range over repetitions), the checks passed, and the saving
against no plugin, marked as noise where the spread of either variant is larger than the difference.

```sh
python3 -m tests.savings_check --workspace /tmp/savings --repetitions 3   # Sonnet 5.5 unless --model
```

Twelve sessions per repetition, on the owner's Claude Code sign-in.

