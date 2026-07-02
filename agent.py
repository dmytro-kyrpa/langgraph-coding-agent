import sys, subprocess, tempfile, os, re, time
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langchain_anthropic import ChatAnthropic

# $ per 1,000,000 tokens (input, output) — ILLUSTRATIVE; set to your provider's real rates.
PRICES = {
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-sonnet-4-6":         (3.00, 15.00),
    "claude-opus-4-8":           (15.00, 75.00),
}
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

def price_of(model):
    return PRICES.get(model, (1.00, 5.00))

def cost_of(state):
    pin, pout = price_of(state["model"])
    return state["in_tokens"] / 1e6 * pin + state["out_tokens"] / 1e6 * pout

# one client per model, created lazily and reused (so the benchmark can swap models)
_llm_cache = {}
def get_llm(model):
    if model not in _llm_cache:
        _llm_cache[model] = ChatAnthropic(model=model)
    return _llm_cache[model]

class State(TypedDict):
    spec: str
    tests: str
    model: str
    plan: str
    plan_decision: str
    plan_feedback: str
    code: str
    result: str
    reason: str
    feedback: str
    attempts: int
    max_attempts: int
    human_decision: str
    in_tokens: int
    out_tokens: int
    total_tokens: int
    token_budget: int
    start_time: float
    auto: bool          # True = run unattended (benchmark): auto-approve, no HITL, no prints

# ---------- helpers ----------
def collect_tests():
    lines = []
    print("Enter the tests (one assert per line; empty line to finish):")
    while True:
        line = input("test> ").strip()
        if line == "":
            break
        lines.append(line)
    return "\n".join(lines)

def ask(prompt, valid):
    while True:
        c = input(prompt).strip().lower()
        if c in valid:
            return c
        print(f"  please type one of: {', '.join(valid)}")

def fix_inputs(state):
    print("\n--- current spec ---"); print(state["spec"])
    ns = input("New spec (Enter to keep): ").strip()
    spec = ns if ns else state["spec"]
    print("--- current tests ---"); print(state["tests"])
    tests = collect_tests() if ask("Re-enter tests? (y/n): ", {"y", "n"}) == "y" else state["tests"]
    return spec, tests

def extract_code(text):
    blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    return blocks[-1].strip() if blocks else text.strip()

def count_tokens(state, r):
    um = r.usage_metadata or {}
    ti, to = um.get("input_tokens", 0), um.get("output_tokens", 0)
    new_total = state["total_tokens"] + ti + to
    if not state.get("auto") and state["token_budget"] and new_total >= 0.8 * state["token_budget"]:
        print(f"  ⚠️  approaching token budget: {new_total}/{state['token_budget']}")
    return ti, to, new_total

# ---------- nodes ----------
def plan(state):
    prompt = f"""You will write a Python function for this spec:
Spec: {state['spec']}
Tests it must pass:
{state['tests']}

Describe your APPROACH in 2-3 short sentences (interpretation, algorithm, edge cases). Do NOT write code yet."""
    if state.get("plan_feedback"):
        prompt += f"\n\nThe human asked you to revise: {state['plan_feedback']}\nRevise accordingly."
    r = get_llm(state["model"]).invoke(prompt)
    ti, to, total = count_tokens(state, r)
    return {"plan": r.content, "plan_feedback": "",
            "in_tokens": state["in_tokens"] + ti, "out_tokens": state["out_tokens"] + to, "total_tokens": total}

def approve_plan(state):
    if state.get("auto"):
        return {"plan_decision": "approve"}                 # unattended: trust the plan
    print("\n--- PROPOSED PLAN ---"); print(state["plan"]); print("---------------------")
    choice = ask("(approve / fix-inputs / revise-plan / abort): ",
                 {"approve", "fix-inputs", "revise-plan", "abort"})
    if choice == "fix-inputs":
        spec, tests = fix_inputs(state)
        return {"spec": spec, "tests": tests, "plan_decision": "revise"}
    if choice == "revise-plan":
        return {"plan_decision": "revise", "plan_feedback": input("What should change in the plan? ").strip()}
    if choice == "approve":
        return {"plan_decision": "approve"}
    return {"plan_decision": "abort"}

def route_plan(state):
    d = state["plan_decision"]
    return "write_code" if d == "approve" else ("plan" if d == "revise" else "abort")

def write_code(state):
    prompt = f"""You are writing a single Python function.

Spec: {state['spec']}

Approved approach:
{state['plan']}

It must pass these tests:
{state['tests']}
"""
    if state.get("feedback"):
        prompt += f"\nContext from before ({state.get('reason','')}):\n{state['feedback']}\nUse it to write correct code.\n"
    prompt += "\nReply with ONLY the Python function code in a single code block — no explanation."
    if not state.get("auto"):
        print(f"\n[Attempt {state['attempts'] + 1}] asking Claude to write code...")
    r = get_llm(state["model"]).invoke(prompt)
    ti, to, total = count_tokens(state, r)
    return {"code": r.content, "attempts": state["attempts"] + 1,
            "in_tokens": state["in_tokens"] + ti, "out_tokens": state["out_tokens"] + to, "total_tokens": total}

def run_tests(state):
    code = extract_code(state["code"])
    script = code + "\n\n" + state["tests"] + "\n"
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(script); path = f.name
    try:
        proc = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=10)
        if proc.returncode == 0:
            reason, err = "", ""
        else:
            err = proc.stderr.strip()
            if "SyntaxError" in err or "IndentationError" in err: reason = "syntax_error"
            elif "AssertionError" in err: reason = "assertion_failure"
            else: reason = "runtime_error"
    except subprocess.TimeoutExpired:
        reason, err = "timeout", "Execution timed out."
    finally:
        os.unlink(path)
    result = "pass" if reason == "" else "fail"
    if not state.get("auto"):
        print(f"[Attempt {state['attempts']}] tests: {result}" + (f" ({reason})" if reason else ""))
    return {"result": result, "reason": reason, "feedback": err}

def should_continue(state):
    if state["result"] == "pass": return "done"
    if state["total_tokens"] >= state["token_budget"]: return "budget"
    if state["attempts"] >= state["max_attempts"]: return "give_up"
    return "retry"

def human(state):
    if state.get("auto"):
        return {"human_decision": "stop"}                   # unattended: give-up = recorded fail
    print("\n=== AGENT GAVE UP ===")
    print(f"After {state['attempts']} attempts the tests still fail ({state['reason']}).")
    print("\n--- last code ---"); print(extract_code(state["code"]))
    print("--- current spec ---"); print(state["spec"])
    print("--- current tests ---"); print(state["tests"]); print("---------------------")
    choice = ask("(fix-inputs / hint / stop): ", {"fix-inputs", "hint", "stop"})
    if choice == "fix-inputs":
        spec, tests = fix_inputs(state)
        return {"spec": spec, "tests": tests, "attempts": 0, "feedback": "", "human_decision": "replan"}
    if choice == "hint":
        return {"feedback": f"Human hint: {input('Your hint: ').strip()}", "attempts": 0, "human_decision": "retry"}
    return {"human_decision": "stop"}

def route_human(state):
    d = state.get("human_decision")
    if d == "replan": return "plan"
    if d == "retry": return "write_code"
    return "stop"

def report(state):
    if state.get("auto"):
        return {}                                           # benchmark does its own reporting
    elapsed = time.time() - state["start_time"]
    cost = cost_of(state)
    status = ("solved ✅" if state.get("result") == "pass"
              else ("stopped 🛑 budget" if state["total_tokens"] >= state["token_budget"] else "ended"))
    print("\n--- FINAL CODE ---")
    print(extract_code(state["code"]) if state.get("code") else "(no code written)")
    print("\n========= SCORECARD =========")
    print(f"  Status:     {status}")
    print(f"  Model:      {state['model']}")
    print(f"  Attempts:   {state['attempts']} (max {state['max_attempts']})")
    print(f"  In tokens:  {state['in_tokens']}")
    print(f"  Out tokens: {state['out_tokens']}")
    print(f"  Total:      {state['total_tokens']} / {state['token_budget']}")
    print(f"  Cost:       ${cost:.4f}")
    print(f"  Time:       {elapsed:.1f}s")
    print("=============================")
    return {}

# ---------- graph ----------
g = StateGraph(State)
for name, fn in [("plan", plan), ("approve_plan", approve_plan), ("write_code", write_code),
                 ("run_tests", run_tests), ("human", human), ("report", report)]:
    g.add_node(name, fn)
g.add_edge(START, "plan")
g.add_edge("plan", "approve_plan")
g.add_conditional_edges("approve_plan", route_plan, {"write_code": "write_code", "plan": "plan", "abort": "report"})
g.add_edge("write_code", "run_tests")
g.add_conditional_edges("run_tests", should_continue,
    {"done": "report", "retry": "write_code", "give_up": "human", "budget": "report"})
g.add_conditional_edges("human", route_human, {"plan": "plan", "write_code": "write_code", "stop": "report"})
g.add_edge("report", END)
app = g.compile()

def _initial(spec, tests, model, max_attempts, token_budget, auto):
    return {
        "spec": spec, "tests": tests, "model": model,
        "plan": "", "plan_decision": "", "plan_feedback": "",
        "code": "", "result": "", "reason": "", "feedback": "",
        "attempts": 0, "max_attempts": max_attempts, "human_decision": "",
        "in_tokens": 0, "out_tokens": 0, "total_tokens": 0, "token_budget": token_budget,
        "start_time": time.time(), "auto": auto,
    }

# programmatic entry point for the benchmark (no prompts, returns final state)
def run_autonomous(spec, tests, model=DEFAULT_MODEL, max_attempts=3, token_budget=10000):
    return app.invoke(_initial(spec, tests, model, max_attempts, token_budget, auto=True),
                      config={"recursion_limit": 50})

# interactive entry point
if __name__ == "__main__":
    print("Enter the function SPEC (one line):")
    spec = input("spec> ")
    tests = collect_tests()
    app.invoke(_initial(spec, tests, DEFAULT_MODEL, max_attempts=3, token_budget=8000, auto=False),
               config={"recursion_limit": 50})
