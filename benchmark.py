import sys, time
from agent import run_autonomous, cost_of, DEFAULT_MODEL

# ============================================================
#  GOLDEN SET — each task has:
#    spec + tests  (correctness oracle)
#    expected      (the performance envelope we test AGAINST)
# ============================================================
GOLDEN_SET = [
    {
        "id": "is_prime",
        "spec": "Write is_prime(n) that returns True if n is a prime number, else False.",
        "tests": ('assert is_prime(7) == True\n'
                  'assert is_prime(4) == False\n'
                  'assert is_prime(1) == False\n'
                  'assert is_prime(2) == True'),
        "expected": {"max_attempts": 1, "max_tokens": 1200, "max_cost": 0.006, "max_latency": 12},
    },
    {
        "id": "reverse_string",
        "spec": "Write reverse_string(s) that returns the string s reversed.",
        "tests": ('assert reverse_string("hello") == "olleh"\n'
                  'assert reverse_string("") == ""\n'
                  'assert reverse_string("a") == "a"'),
        "expected": {"max_attempts": 1, "max_tokens": 900, "max_cost": 0.005, "max_latency": 12},
    },
    {
        "id": "count_vowels",
        "spec": "Write count_vowels(s) that returns the number of vowels (a,e,i,o,u) in s, case-insensitive.",
        "tests": ('assert count_vowels("hello") == 2\n'
                  'assert count_vowels("xyz") == 0\n'
                  'assert count_vowels("AEIOU") == 5'),
        "expected": {"max_attempts": 1, "max_tokens": 1000, "max_cost": 0.005, "max_latency": 12},
    },
    {
        "id": "roman",
        "spec": "Write roman(n) converting an integer 1-3999 to an uppercase Roman numeral string.",
        "tests": ('assert roman(4) == "IV"\n'
                  'assert roman(9) == "IX"\n'
                  'assert roman(40) == "XL"\n'
                  'assert roman(1994) == "MCMXCIV"'),
        "expected": {"max_attempts": 2, "max_tokens": 2000, "max_cost": 0.010, "max_latency": 18},
    },
    {
        "id": "is_valid_ipv4",
        "spec": "Write is_valid_ipv4(s) returning True if s is a valid IPv4 address (reject leading zeros), else False.",
        "tests": ('assert is_valid_ipv4("192.168.1.1") == True\n'
                  'assert is_valid_ipv4("256.1.1.1") == False\n'
                  'assert is_valid_ipv4("192.168.01.1") == False\n'
                  'assert is_valid_ipv4("1.2.3") == False'),
        "expected": {"max_attempts": 2, "max_tokens": 2000, "max_cost": 0.010, "max_latency": 18},
    },
    {
        "id": "number_to_words",
        "spec": "Write number_to_words(n) converting an integer 0-9999 to lowercase English words, hyphen for compound tens, no 'and'.",
        "tests": ('assert number_to_words(0) == "zero"\n'
                  'assert number_to_words(42) == "forty-two"\n'
                  'assert number_to_words(100) == "one hundred"\n'
                  'assert number_to_words(1234) == "one thousand two hundred thirty-four"'),
        "expected": {"max_attempts": 2, "max_tokens": 2800, "max_cost": 0.015, "max_latency": 22},
    },
]

BASELINE_MODEL = "claude-sonnet-4-6"   # stronger/pricier reference for shadow mode


def _run(task, model):
    """Run one golden task on one model; return a compact result row."""
    t0 = time.time()
    st = run_autonomous(task["spec"], task["tests"], model=model,
                        max_attempts=3, token_budget=10000)
    return {
        "solved": st["result"] == "pass",
        "first_try": st["result"] == "pass" and st["attempts"] == 1,
        "attempts": st["attempts"],
        "tokens": st["total_tokens"],
        "cost": cost_of(st),
        "time": time.time() - t0,
    }


def check_envelope(r, exp):
    """Return the list of expected metrics the run EXCEEDED (empty = within budget)."""
    over = []
    if r["attempts"] > exp["max_attempts"]:
        over.append(f"attempts {r['attempts']}>{exp['max_attempts']}")
    if r["tokens"] > exp["max_tokens"]:
        over.append(f"tokens {r['tokens']}>{exp['max_tokens']}")
    if r["cost"] > exp["max_cost"]:
        over.append(f"cost ${r['cost']:.4f}>${exp['max_cost']:.3f}")
    if r["time"] > exp["max_latency"]:
        over.append(f"time {r['time']:.0f}s>{exp['max_latency']}s")
    return over


# ============================================================
#  MODE 1 — single-model benchmark against expected envelopes
# ============================================================
def run_benchmark(model=DEFAULT_MODEL):
    print(f"Benchmark — {len(GOLDEN_SET)} tasks on {model} (each checked against its envelope)\n")
    rows = []
    for task in GOLDEN_SET:
        r = _run(task, model)
        over = check_envelope(r, task["expected"]) if r["solved"] else []
        status = "PASS ✅" if (r["solved"] and not over) else ("OVER ⚠️" if r["solved"] else "FAIL ❌")
        rows.append({**r, "id": task["id"], "status": status, "over": over})
        line = (f"  {task['id']:<18} {status:<8} "
                f"attempts={r['attempts']} tokens={r['tokens']} "
                f"cost=${r['cost']:.4f} time={r['time']:.1f}s")
        if over:
            line += f"   ⚠️ over: {', '.join(over)}"
        print(line)

    n = len(rows)
    solved_n = sum(r["solved"] for r in rows)
    first_n = sum(r["first_try"] for r in rows)
    pass_n = sum(r["status"].startswith("PASS") for r in rows)
    over_n = sum(r["status"].startswith("OVER") for r in rows)
    total_cost = sum(r["cost"] for r in rows)

    print("\n=============== BENCHMARK SUMMARY ===============")
    print(f"  Model:                    {model}")
    print(f"  Pass@k  (solved at all):  {solved_n}/{n}  ({solved_n/n*100:.0f}%)")
    print(f"  Pass@1  (solved 1st try): {first_n}/{n}  ({first_n/n*100:.0f}%)")
    print(f"  Within envelope (PASS):   {pass_n}/{n}")
    print(f"  Solved-but-over (OVER):   {over_n}/{n}")
    print(f"  Avg cost / task:          ${total_cost/n:.4f}")
    print(f"  Avg time / task:          {sum(r['time'] for r in rows)/n:.1f}s")
    print(f"  Total cost:               ${total_cost:.4f}")
    print("================================================")


# ============================================================
#  MODE 2 — shadow mode: run the SAME golden set on two models
#  and compare quality vs cost ("X% more solved at Y× the cost")
# ============================================================
def run_shadow(primary=DEFAULT_MODEL, baseline=BASELINE_MODEL):
    print(f"Shadow-mode — primary={primary}  vs  baseline={baseline}\n")
    print(f"  {'task':<18} {'PRIMARY':<28} BASELINE")

    def fmt(r):
        return f"{'PASS' if r['solved'] else 'FAIL'} a={r['attempts']} ${r['cost']:.4f} {r['time']:.1f}s"

    p_solved = b_solved = 0
    p_cost = b_cost = 0.0
    p_time = b_time = 0.0
    for task in GOLDEN_SET:
        p = _run(task, primary)
        b = _run(task, baseline)
        p_solved += p["solved"]; b_solved += b["solved"]
        p_cost += p["cost"];     b_cost += b["cost"]
        p_time += p["time"];     b_time += b["time"]
        print(f"  {task['id']:<18} {fmt(p):<28} {fmt(b)}")

    n = len(GOLDEN_SET)
    print("\n=============== SHADOW SUMMARY ===============")
    print(f"  Primary  {primary}")
    print(f"    solved {p_solved}/{n} ({p_solved/n*100:.0f}%)  total ${p_cost:.4f}  avg {p_time/n:.1f}s")
    print(f"  Baseline {baseline}")
    print(f"    solved {b_solved}/{n} ({b_solved/n*100:.0f}%)  total ${b_cost:.4f}  avg {b_time/n:.1f}s")
    if p_cost > 0 and b_cost > 0:
        pct = p_cost / b_cost * 100
        mult = b_cost / p_cost
        print(f"\n  → Primary solved {p_solved/n*100:.0f}% vs {b_solved/n*100:.0f}%, "
              f"at {pct:.0f}% of baseline cost ({mult:.1f}× cheaper).")
        delta = b_solved - p_solved
        if delta > 0:
            print(f"    Baseline solved {delta} more task(s) — the cost/quality trade-off to decide on.")
        elif delta == 0:
            print(f"    Same tasks solved — the cheaper model is the clear choice here.")
    print("=============================================")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "shadow":
        run_shadow()
    else:
        run_benchmark()
