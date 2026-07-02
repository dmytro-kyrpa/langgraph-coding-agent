# Self-Correcting Coding Agent + Benchmark (LangGraph)

Three capabilities, built to demonstrate the agentic-PM toolkit end-to-end: **(1) a self-correcting
coding agent**, **(2) a benchmark** that grades it against expected-performance envelopes, and
**(3) a shadow mode** that compares a cheap vs. a stronger model on cost and quality.

### 1. The agent (`agent.py`)
An agentic coding assistant built in **LangGraph**. Give it a **function spec + tests**; it proposes
a plan, writes the code, runs the tests in an isolated subprocess, classifies any failure (syntax / runtime /
assertion / timeout), and **fixes itself in a loop** until they pass — or **escalates to a human**.
It is contained by real guardrails: an **attempt limit**, a **token/cost circuit breaker** with an
80% notifier, **two human-in-the-loop gates**, and an end-of-run **scorecard** (status, attempts,
in/out tokens, `$`, time). Same shape as production coding agents (write → test → fix → loop), on
trivial tasks so the *mechanics* stay visible.

**Model:** the agent is powered by **Anthropic's Claude** — `claude-haiku-4-5` by default, called
through LangChain's `ChatAnthropic` wrapper. It's swappable in **one line** (`agent.py`), which is
exactly what shadow mode uses to compare models on the same tasks.

### 2. The benchmark (`benchmark.py`)
A **golden-set benchmark** that runs the same agent **autonomously** (no human prompts) over a fixed
set of tasks — but it measures more than pass/fail. Each task carries an **expected envelope**
(max attempts / tokens / cost / latency), and every run is scored **against** it: **PASS** (solved &
within budget), **OVER** (solved but blew a threshold — a regression signal), **FAIL** (unsolved). It
reports **Pass@1 vs Pass@k** and average cost/time. This turns "does it work?" into "does it work
*within the agreed cost and latency budget?*" — a **regression / SLA test**.

### 3. Shadow mode (`python benchmark.py shadow`)
A mode of the benchmark that runs the same tasks on a **cheap primary** model *and* a **stronger
baseline**, and compares them on **cost *and* quality**. It answers the money question every LLM
product faces: *is the cheap model good enough to ship?* Same quality at lower cost → the cheap model
wins; a quality gap → those failures are exactly the tasks that would need a human or the pricier
model. It's the cost/quality trade-off that decides real LLM-product economics.

The agent and the benchmark are **not separate codebases** — the benchmark imports the agent
(`from agent import run_autonomous`) and drives it unattended; shadow mode is just the benchmark run
across two models.

---

## What it does
1. You give it a **spec** (one line) + **tests** (assert lines).
2. It **proposes a plan**; you **approve / fix-inputs / revise / abort** *(human-in-the-loop #1)*.
3. It **writes code**, **runs the tests** in an isolated subprocess, and **classifies any failure**
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
| `run_tests` | **oracle** — runs code + tests in an isolated subprocess; classifies failures |
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
| **Constrain** | structured prompt + approved plan + isolated subprocess + re-ask input guards |
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
added value), plus avg cost/time. This is **Layer-2 evaluation at scale** — the basis for regression
testing (and for the [shadow-mode](#shadow-mode--cheap-model-vs-strong-model-python-benchmarkpy-shadow)
comparison below).

```
  is_prime           PASS ✅   attempts=1 tokens=626 cost=$0.0016 time=3.2s
  reverse_string     PASS ✅   attempts=1 tokens=388 cost=$0.0008 time=4.2s
  count_vowels       PASS ✅   attempts=1 tokens=477 cost=$0.0010 time=2.5s
  roman              PASS ✅   attempts=1 tokens=710 cost=$0.0020 time=4.3s
  is_valid_ipv4      PASS ✅   attempts=1 tokens=771 cost=$0.0021 time=3.4s
  number_to_words    PASS ✅   attempts=1 tokens=1130 cost=$0.0038 time=6.7s

  Pass@k: 6/6 (100%)   Pass@1: 6/6 (100%)   Within envelope: 6/6
```
*(OVER ⚠️ and FAIL ❌ are the other two statuses — shown when a run blows its envelope or doesn't solve the task.)*

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

## Shadow mode — cheap model vs. strong model (`python benchmark.py shadow`)
The same agent is run over the golden set **twice** — once on a cheap **primary** model, once on a
stronger **baseline** — and the two runs are compared on **quality *and* cost**. Both run
**autonomously** (no human on either side), so the only variable is the model — the fixed spec+tests
per task are the **common ground** that makes the comparison fair.

```
Shadow-mode — primary=claude-haiku-4-5  vs  baseline=claude-sonnet-4-6

  task               PRIMARY                   BASELINE
  is_prime           PASS a=1 $0.0024 3.9s     PASS a=1 $0.0050 6.5s
  reverse_string     PASS a=1 $0.0008 2.5s     PASS a=1 $0.0026 7.9s
  count_vowels       PASS a=1 $0.0011 3.2s     PASS a=1 $0.0042 6.0s
  roman              PASS a=1 $0.0027 5.5s     PASS a=1 $0.0058 7.5s
  is_valid_ipv4      PASS a=1 $0.0025 3.5s     PASS a=1 $0.0049 6.8s
  number_to_words    PASS a=1 $0.0033 5.0s     PASS a=1 $0.0088 9.5s

  Primary  claude-haiku-4-5   solved 6/6 (100%)  total $0.0128  avg 3.9s
  Baseline claude-sonnet-4-6  solved 6/6 (100%)  total $0.0315  avg 7.4s
  → Primary solved 100% vs 100%, at 41% of baseline cost (2.5× cheaper).
```

**The point:** cheap tokens aren't the whole story — a cheap model's *real* cost includes the human
time (or fallback) needed to rescue the tasks it fails. So the honest read is **quality *and* cost
together**: here the cheap model matched the strong one at **2.5× less cost** (and faster), so it's
the clear choice. Where a gap *does* appear, the cheap model's **failure rate is the stand-in for the
hidden human cost** — those are exactly the tasks that would need a person or the pricier model.

---

## Design notes (learned by building + breaking it)
- **test-pass ≠ correct.** Given a wrong test, the agent will *game* it (e.g. `if n==4: return True`).
  A plan-gate helps but doesn't fully stop it — the real fixes are **correcting the oracle** and
  **spec-match** (checking intent, not just asserts).
- **A human-in-the-loop must let the human *act*** (fix the actual test), not just comment.
- **Robust code extraction** (models wrap in fences / add prose) and **input sanitization** (stray
  whitespace breaks execution) are real guardrails, not afterthoughts.
- **Every metric needs a yardstick** — hence the benchmark's *expected envelopes* (a constructed baseline).
- **You can't benchmark with a human in the loop.** A human helps each model *differently*, so it
  contaminates the comparison — you'd be measuring the human, not the model. The benchmark runs the
  agent autonomously on both sides; the human's role in real use is to *shape the common ground*
  (spec + tests), which is then frozen before the fair comparison runs.
- **Honest scope (not overclaimed):** test execution uses **process isolation + a timeout**, not a
  true sandbox — real hardening (container / seccomp / no network) is the production step; fine here
  because tasks are trusted. And the interactive/autonomous split is a simple `auto` flag on the
  state — pragmatic for a prototype; the cleaner design injects an execution *policy* so the agent
  stays unaware of its caller.

---

## Run it
```bash
git clone https://github.com/dmytro-kyrpa/langgraph-coding-agent.git
cd langgraph-coding-agent
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."      # from console.anthropic.com

python agent.py               # interactive: type a spec + tests, approve the plan, watch it solve
python benchmark.py           # golden set on one model → PASS/OVER/FAIL vs expected envelopes
python benchmark.py shadow    # golden set on cheap vs. strong model → cost/quality comparison
```
> Model prices (`PRICES` in `agent.py`) are **illustrative** — set them to your provider's real rates.

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
- **N-run averaging** for statistically stable pass rates (LLM output is non-deterministic).
- **LangFuse** tracing/eval instrumentation.
