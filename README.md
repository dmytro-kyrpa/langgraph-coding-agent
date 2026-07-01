# Self-Correcting Coding Agent + Benchmark (LangGraph)

Two things, built to demonstrate the agentic-PM toolkit end-to-end: **(1) a self-correcting coding
agent**, and **(2) a benchmark that runs that agent against expected-performance envelopes.**

### 1. The agent (`agent.py`)
An agentic coding assistant built in **LangGraph**. Give it a **function spec + tests**; it proposes
a plan, writes the code, runs the tests in a sandbox, classifies any failure (syntax / runtime /
assertion / timeout), and **fixes itself in a loop** until they pass — or **escalates to a human**.
It is contained by real guardrails: an **attempt limit**, a **token/cost circuit breaker** with an
80% notifier, **two human-in-the-loop gates**, and an end-of-run **scorecard** (status, attempts,
in/out tokens, `$`, time). Same shape as production coding agents (write → test → fix → loop), on
trivial tasks so the *mechanics* stay visible.

**Model:** the agent is powered by **Anthropic's Claude** — `claude-haiku-4-5` by default, called
through LangChain's `ChatAnthropic` wrapper. It's swappable in **one line** (`agent.py`), which is
exactly what the shadow-mode benchmark exploits to compare models on the same tasks.

### 2. The benchmark (`benchmark.py`)
A **golden-set benchmark** that runs the same agent **autonomously** (no human prompts) over a fixed
set of tasks — but it measures more than pass/fail. Each task carries an **expected envelope**
(max attempts / tokens / cost / latency), and every run is scored **against** it: **PASS** (solved &
within budget), **OVER** (solved but blew a threshold — a regression signal), **FAIL** (unsolved). It
reports **Pass@1 vs Pass@k** and average cost/time. This turns "does it work?" into "does it work
*within the agreed cost and latency budget?*" — a **regression / SLA test**, and the basis for
**shadow-mode** model comparisons ("X% more solved at Y× the cost").

The agent and the benchmark are **not two codebases** — the benchmark imports the agent
(`from agent import run_autonomous`) and drives it in an unattended mode built into the same graph.

---

## What it does
1. You give it a **spec** (one line) + **tests** (assert lines).
2. It **proposes a plan**; you **approve / fix-inputs / revise / abort** *(human-in-the-loop #1)*.
3. It **writes code**, **runs the tests** in a sandboxed subprocess, and **classifies any failure**
   (syntax / runtime / assertion / timeout).
4. On failure with attempts left → it **loops back** with the error as feedback.
5. On failure after `max_attempts` → it **escalates to a human** *(HITL #2: fix-inputs / hint / stop)*.
6. A **token budget** caps cost (circuit breaker) with an **80% notifier**.
7. Every run ends at a **scorecard**: status, attempts, in/out tokens, `$` cost, time.

---

## Architecture

| Node | Role |
|---|---|
| `plan` | proposes an approach (interpretation + algorithm + edge cases) |
| `approve_plan` | **HITL #1** — approve / fix-inputs / revise-plan / abort |
| `write_code` | writes/fixes the function (uses the approved plan + error feedback) |
| `run_tests` | **oracle** — runs code + tests in a sandboxed subprocess; classifies failures |
| `human` | **HITL #2** — give-up handoff: fix-inputs / hint / stop |
| `report` | **eval scorecard** — status / attempts / in-out tokens / cost / time |

### Flow
```
                         START
                           │
                           ▼
              ┌────────►  PLAN  ◄──────────────────────┐
              │            │                            │  re-plan
              │            ▼                            │  (revise-plan /
              │      APPROVE_PLAN  ──── abort ─────┐    │   fix-inputs)
              │       (HITL #1)                    │    │
              │         │ approve                  │    │
              │         ▼                          │    │
              │      WRITE_CODE  ◄─────────┐       │    │
              │         │  (⚠️ 80% budget notifier)     │
              │         ▼                  │ retry │    │
              │      RUN_TESTS             │ (loop)│    │
              │         │  (oracle+classify)│      │    │
              │         ▼                  │       │    │
              │   should_continue ─────────┘       │    │
              │     │     │     │                   │    │
              │ pass│     │     │ give_up           │    │
              │     │ 🛑 BUDGET │                   │    │
              │     │ (tokens   │                   │    │
              │     │  ≥ cap)   ▼                   │    │
              │     │     │   HUMAN (HITL #2)       │    │
              │     │     │    ├─ fix-inputs ───────┼────┘
              │     │     │    ├─ hint ─────────────┘  (→ write_code)
              │     │     │    └─ stop ──────┐
              │     ▼     ▼                  ▼
              │   ┌─────────────────────────────┐
              └──►│           REPORT            │  ◄─ pass / budget / abort / stop
                  │  scorecard: status, tokens, │
                  │  $, time                    │
                  └──────────────┬──────────────┘
                                 ▼
                                END
```

### Guardrails (the PM toolkit)
| Lever | Where |
|---|---|
| **Verify** (oracle + failure classification) | `run_tests` |
| **Limit — attempts** | `should_continue` (`max_attempts`) |
| **Limit — cost** | token-budget **circuit breaker** → `report` |
| **Notifier** | 80% budget warning inside the LLM nodes |
| **Constrain** | structured prompt + approved plan + sandboxed subprocess + re-ask input guards |
| **HITL ×2** | `approve_plan` (start gate) + `human` (give-up handoff) — both let the human **fix the oracle** |
| **Eval — Layer 1 (in-run)** | `run_tests` |
| **Eval — Layer 2 (the run)** | `report` scorecard |

---

## The benchmark (`benchmark.py`)
Runs the agent **autonomously** over a **golden set** of tasks. Each task carries an **expected
envelope** (max attempts / tokens / cost / latency), and the run is scored against it:

- **PASS ✅** — solved *and* within the expected envelope
- **OVER ⚠️** — solved, but blew a threshold (a **regression** signal — reports *which*)
- **FAIL ❌** — didn't solve

It reports **Pass@1** (solved first try = raw capability) vs **Pass@k** (solved at all = the loop's
added value), plus avg cost/time. This is **Layer-2 evaluation at scale** — the basis for
regression testing and **shadow-mode** comparisons across models ("X% more solved at Y× the cost").

```
  is_prime           PASS ✅   attempts=1 tokens=780 cost=$0.0016 time=3.9s
  is_valid_ipv4      OVER ⚠️   attempts=2 tokens=2100 ...   ⚠️ over: tokens 2100>2000
  ...
  Pass@k: 6/6 (100%)   Pass@1: 5/6 (83%)   Within envelope: 5/6
```

### Why it matters

The agent produces the **solution**; the benchmark is the **yardstick** that grades it. The task
itself is fixed by *me* — the golden set defines both the **correct answer** (tests) *and* the
**acceptable cost/latency budget** (the envelope). So it measures two dimensions, not one:

| Dimension | Question | Signal |
|---|---|---|
| **Correctness** | did it solve the task? | the tests → Pass@1 / Pass@k |
| **Efficiency** | did it solve it *within budget*? | the envelope → PASS / OVER / FAIL |

Most eval stops at the first row. The second is where the economics live: an agent that solves 100%
at 6 attempts and $0.05/task can be *worse* for the business than one that solves 90% at $0.002.

One agent run is an anecdote. Running the **same fixed set every time** makes the number repeatable
and comparable — which is what unlocks the actual uses:

1. **Regression / CI gate** — change a prompt, model, or guardrail → rerun → catch quality drops or
   cost blow-ups *before* shipping, instead of tuning on vibes.
2. **Model selection (shadow-mode)** — same golden set on Haiku vs a stronger model → *"B solves 15%
   more at 4× the cost"* → a data-backed buy decision.
3. **SLA & cost forecasting** — the envelopes let you promise *"~83% first-try, ~$0.002/task, p95
   12s"* — what you need to price the feature and set a customer SLA.
4. **Tuning with evidence** — did a change actually help? Measured, not guessed.

---

## Design notes (learned by building + breaking it)
- **test-pass ≠ correct.** Given a wrong test, the agent will *game* it (e.g. `if n==4: return True`).
  A plan-gate helps but doesn't fully stop it — the real fixes are **correcting the oracle** and
  **spec-match** (checking intent, not just asserts).
- **A human-in-the-loop must let the human *act*** (fix the actual test), not just comment.
- **Robust code extraction** (models wrap in fences / add prose) and **input sanitization** (stray
  whitespace breaks execution) are real guardrails, not afterthoughts.
- **Every metric needs a yardstick** — hence the benchmark's *expected envelopes* (a constructed baseline).

---

## Run it
```bash
git clone <your-repo-url>
cd langgraph-coding-agent
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."      # from console.anthropic.com

python agent.py         # interactive: type a spec + tests, approve the plan, watch it solve
python benchmark.py     # runs the agent autonomously over the golden set + scores it
```

---

## Roadmap
- **Externalize the golden set** — move the tasks + envelopes out of `benchmark.py` into a data
  source (JSON → SQLite / Postgres / MongoDB Atlas), so the set is versioned and edited without
  touching code, and can grow beyond what fits in a file.
- **Closed feedback loop** — persist every run (result, attempts, tokens, cost, latency) back to
  that store, then use the accumulated history to **(a) re-derive envelopes** from real behavior
  instead of hand-set guesses, and **(b) promote newly-discovered failure cases** into the golden
  set. Each promotion stays **human-reviewed** — so the graded set never becomes something the agent
  itself can influence, keeping the oracle independent (see *test-pass ≠ correct*, above).
- **Shadow-mode**: run the golden set across models (Haiku vs. a stronger model) → cost/quality trade-off.
- **N-run averaging** for statistically stable pass rates.
- **LangFuse** tracing/eval instrumentation.
