# Presentation demo

Two pages that make the harness legible to an audience. This is presentation
tooling, not part of the scored experiment: it never writes to `data/runs/`, never
recomputes the judge, and changes nothing about how runs are scored.

```bash
uv sync --group demo
uv run uvicorn demo.server:app --port 8000
```

- `http://localhost:8000/` — **Walkthrough.** Replays four curated held-out
  Coca-Cola questions step by step against a pipeline diagram, showing the
  reference answer, a value-by-value comparison, findings, and metrics for
  each. Needs nothing beyond the repository files.
- `http://localhost:8000/chat` — **Ask.** A live, multi-turn chat covering
  every company in the frozen data. Each turn is routed automatically to the
  right filing (by name, ticker, or description), so you never pick a
  company; follow-ups like "and the year before?" resolve using conversation
  history. Chat history is saved via Amazon Bedrock AgentCore Memory, so old
  chats can be reopened later. Needs AWS + Langfuse credentials and a built
  Qdrant collection — availability is reported by `GET /api/health`.

## The demo in plain words

Three big companies — Coca-Cola, Google (its official name is Alphabet), and
Amazon — each publish one long, official report every year about how much money
they made and spent. This demo is a small website that answers questions about
those reports, like *"How much did Coca-Cola earn in 2025?"*

The key idea: the demo is not allowed to guess, and it does not go looking around
the internet. It can only use those official reports, and for every number it
gives you, it shows exactly where in the report that number came from — like
keeping the receipt for every number.

**There are two pages:**

- **Walkthrough** — a replay. It shows a few example questions that were already
  answered and checked by a person. You press play and watch each step happen,
  and it puts the correct answer next to the demo's answer so you can compare
  them yourself.
- **Ask** — you type your own question and watch it get answered right there,
  live. You don't have to know which reports it has: it looks that up itself and
  works out which company you mean, whether you name it, use its ticker, or just
  describe it ("the maker of Sprite").

**How the demo answers a question — the main idea.** Think of it as a tiny team
where each member has one job and hands the work to the next:

1. **A router** first checks which companies there are reports for, then works
   out which one your question is about. If it isn't about any of them, it just
   answers you in plain words instead of looking anything up.
2. **A researcher** reads that company's report and finds the facts that matter,
   such as the profit for a year.
3. **A calculator** does any math that's needed. It uses a real calculator, so
   the numbers are exact — it never just eyeballs them.
4. **A checker** looks over all the work before you see it. If something is
   wrong, it sends the work back to be fixed — but only one time.
5. Once the work passes the check, the **final answer** is written out for you.

While that is happening, the page shows simple words like *Searching*,
*Calculating*, and *Checking* so you can follow along. If you want the details,
you can open a section that lists every small step it took.

**It remembers the conversation.** After it answers, you can ask a follow-up like
*"and the year before?"* and it knows what you mean, because it remembers what you
already asked. Your past chats are saved (in an Amazon memory service), so you can
close the page, come back later, and open an old chat again right where you left
off.

**A few simple rules it always follows:**

- It uses the same fixed reports every time, so the answers don't change from one
  day to the next.
- Every number comes with proof of where it came from.
- It answers one question at a time.
- If the fact isn't in the report, it says so instead of making something up.

## Technologies used

**Front end** — plain HTML/CSS/JavaScript, no framework or build step.
Server-Sent Events (SSE) stream live progress (*Searching*, *Calculating*,
*Checking*) to a hand-drawn SVG pipeline diagram.

**Back end** — Python, served by **Starlette + uvicorn** (`sse-starlette` for
the SSE stream), running the "team of helpers" built with the **Strands
Agents SDK**.

**AWS** — **Amazon Bedrock** hosts the model (**Claude Sonnet 4.5**); **Bedrock
AgentCore Memory** saves chat history; **Bedrock Guardrails** is an optional
safety filter.

**Other** — **Qdrant** (local vector search over the reports), **fastembed**
(text → vectors for Qdrant), **Langfuse** (records runs for debugging, doesn't
affect answers).

Only the **Ask** page uses Bedrock, Qdrant, Langfuse, and AgentCore Memory;
the **Walkthrough** page replays saved files and needs none of them.

### The five steps that build one answer

```text
   Your question
        │
        ▼
 [0] ROUTE ───── lists the filings it has, picks the company
        │         (no company? plain reply, and it stops here)
        ▼
 [1] RESEARCH  ── finds the facts in the frozen report
        │
        ▼
 [2] CALCULATE ── does exact math with a real calculator
        │
        ▼
 [3] CHECK ───── reviews the work before you see it
        │  │
        │  └──► if something is wrong, send back to step 2  (only once)
        ▼
 [4] ANSWER ──── written out, with proof for every number
```

## What needs which services

| Feature | Requires |
| --- | --- |
| Walkthrough page | Nothing beyond the repository files |
| Ask page | AWS credentials, Langfuse credentials, and Qdrant with the built collection |
| Ask-page chat history | Amazon Bedrock AgentCore Memory (optional; best-effort) |

Availability is reported by `GET /api/health`; `?refresh=1` retries after
fixing credentials. If AgentCore Memory is unreachable, the Ask page still
answers, it just keeps history for the current browser session only.

`/api/health` checks Langfuse and Qdrant and caches the result; it does not
make a live Bedrock call. An AWS session can expire after it last reported
ready, so it stays reported as available until an actual question is asked —
that turn then shows the red "No AWS Bedrock connection" error described
above, rather than `/api/health` catching it first.

## Recommended questions

`RECOMMENDED_QUESTIONS` in `presentation.py` holds three questions, one per
company and one per question type, each deliberately sitting in a gap in the
frozen benchmark so clicking it always runs the pipeline for real. The three
questions and their correct answers, computed from the frozen facts:

| Company | Type | Question | Correct answer |
| --- | --- | --- | --- |
| Coca-Cola | single formula | What was Coca-Cola's net income as a percentage of total assets in fiscal 2025? | **12.50%** — net income $13,107M (`fact-ko-net-income-fy2025`) ÷ total assets $104,816M (`fact-ko-total-assets-fy2025`). |
| Alphabet | period comparison | How did Alphabet's operating income change between fiscal 2023 and fiscal 2024 in dollar and percentage terms? | **+$28,097M (+33.33%)** — from $84,293M (`fact-googl-operating-income-fy2023`) to $112,390M (`fact-googl-operating-income-fy2024`). |
| Amazon | multi-step | What was Amazon's average annual net income for fiscal 2023 through fiscal 2025, and how did fiscal 2025 compare with that average? | **Average $55,781M; FY2025 was $21,889M (+39.24%) above it** — net income $30,425M (`fact-amzn-net-income-fy2023`), $59,248M (`fact-amzn-net-income-fy2024`), $77,670M (`fact-amzn-net-income-fy2025`). |

All three are verified live: 22–27 seconds and $0.04–$0.06 each.

## Implementation notes

- **Routing** (`demo/routing.py`) builds its company catalog from the frozen
  data itself (no hardcoded list), runs on every turn, and emits the same
  trace events as the pipeline stages so routing decisions are auditable. It
  is demo-side only — the scored harness always receives an explicit ticker.
  Its only decision is which company, if any, a message is about: it never
  sees, and must never claim, which years or figures a filing covers — a
  10-K's numbers span more years than the one it was filed for, and only the
  pipeline that reads the filing can tell what it holds. If the router's own
  model call fails (expired credentials, no Bedrock connection, a throttle),
  the Ask page shows that plainly as a red error rather than a misleading
  "which company?" reply.
- **Conversation history** is compacted by the harness
  (`DECOMPOSED_CONTEXT_MAX_TURNS`, per-answer char limits) before being fed to
  the research/math/verifier stages, so long chats can't grow prompts
  unbounded. A benchmark run passes no history, so its stage prompts are
  unaffected.
- **Chat storage** (`demo/agentcore_memory.py`) uses AgentCore Memory
  sessions/events rather than local disk, exposed via `/api/conversations`
  and `/api/conversation/{id}` (list/get/delete). Every call is best-effort.
- **Data provenance**: walkthrough narration and metrics come from
  `data/presentation-candidates/candidates.json`; everything else is
  re-evaluated at startup from the batch it names. Answers come from one
  shared `DecomposedAgentHarness` run with `persist=False`, so nothing typed
  into the Ask page ever reaches `data/runs/`.
