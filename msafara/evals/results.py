"""Write the results page from the run log, so every number on it comes from a logged run.

  python -m msafara.evals.results            # docs/eval-results.md + the README results block

Picks the latest 100-trip run of each system (rules engine, LLM agents) from evals/runs.jsonl.
"""
import re
from pathlib import Path

from .run import history

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "docs" / "eval-results.md"
README = ROOT / "README.md"
START, END = "<!-- RESULTS:START -->", "<!-- RESULTS:END -->"


def latest(mode: str, n: int = 100):
    runs = [r for r in history() if r["mode"] == mode and r["n_cases"] == n and not r["intake_only"]
            and not r["label"].startswith("dress rehearsal")]
    return runs[-1] if runs else None


def _pct(x):
    if x is None:
        return "-"
    return f"{x:.0%}" if x in (0, 1) else f"{x:.1%}"


def _num(x):
    return "-" if x is None else f"{x:,}"


ROWS = [
    ("Task completion (right end state, no breach)", lambda m: _pct(m.get("task_completion_rate"))),
    ("  core 25 trips", lambda m: _pct(m.get("completion_core25"))),
    ("  75 messy / adversarial trips", lambda m: _pct(m.get("completion_extended"))),
    ("Intake field accuracy", lambda m: _pct(m["extraction_field_accuracy"])),
    ("Intake exact (all 8 fields right)", lambda m: _pct(m["extraction_exact"])),
    ("Intake, 12 held-out phrasings (field accuracy)", lambda m: _pct(m.get("heldout_extraction_field_accuracy"))),
    ("Price-limit violations", lambda m: _num(m.get("price_limit_violations"))),
    ("Sends without approval (must be 0)", lambda m: f"{m['unapproved_sends']} of {m['messages_sent']:,}"),
    ("Escalation precision / recall (trip)", lambda m: f"{m['escalation_precision']:.2f} / {m['escalation_recall']:.2f}"),
    ("Escalation precision / recall (service)", lambda m: f"{m['service_escalation_precision']:.2f} / {m['service_escalation_recall']:.2f}"),
    ("Vendor-reply parse accuracy", lambda m: _pct(m.get("reply_parse_accuracy"))),
    ("Outbound message quality (same model judge, both columns)", lambda m: _pct(m.get("message_quality_pass_rate"))),
    ("Model calls per trip", lambda m: f"{m['per_trip']['llm_calls']:g}"),
    ("Tokens per trip (in / out)", lambda m: f"{m['per_trip']['input_tokens']:,} / {m['per_trip']['output_tokens']:,}"),
    ("Cost per trip", lambda m: "$0" if not m["per_trip"]["llm_calls"] else
     (f"${m['per_trip']['cost_usd']:.4f}" if m["per_trip"]["cost_usd"] is not None else "not priced")),
    ("Wall time per trip", lambda m: f"{m['per_trip']['seconds']:.1f} s"),
]


def table(rules, agents) -> str:
    out = ["| Metric | Rules engine | LLM agents + rules guardrails |", "|---|---|---|"]
    for name, f in ROWS:
        out.append(f"| {name} | {f(rules['metrics']) if rules else '-'} | {f(agents['metrics']) if agents else 'not run yet'} |")
    return "\n".join(out)


def run_line(r) -> str:
    return (f"{r['ts'][:10]}, {r['n_cases']} trips, model: {r['model_class']}, prompts "
            f"{', '.join(f'{k} {v}' for k, v in sorted(r['prompt_versions'].items()))}, commit `{r.get('git') or '?'}`")


def page(rules, agents) -> str:
    a = agents["metrics"] if agents else None
    dec = a["decisions"] if a else {}
    lines = [
        "# Eval results: LLM agents vs the rules engine",
        "",
        "Same 100 synthetic trips, same simulated vendors, same checks. The only difference is who reads and decides.",
        "",
        table(rules, agents),
        "",
        "**Runs**",
        f"- Rules engine: {run_line(rules) if rules else 'not run'}",
        f"- LLM agents: {run_line(agents) if agents else 'not run yet'}",
        "",
    ]
    if a:
        lines += [
            "**On the LLM run**",
            f"- Service decisions made by the negotiation agent: {dec.get('llm', 0)}; handed to the rules fallback after a model "
            f"failure: {dec.get('rules_fallback', 0)}.",
            f"- Tool calls refused by a guardrail inside a tool (then corrected or escalated): {a.get('tool_refusals', 0)}.",
            f"- Trips that crashed: {a.get('crashed', 0)}.",
            "",
            "**What the LLM run still got wrong** (every non-wording failure, from the run log):",
        ]
        misses = [(f["id"], w) for f in agents["failed_cases"] for w in f["why"] if not w.startswith("message quality")]
        lines += [f"- `{cid}`: {w[:160]}" for cid, w in misses] or ["- nothing outside message wording"]
        wq = sum(1 for f in agents["failed_cases"] if any(w.startswith("message quality") for w in f["why"]))
        lines += [f"- Message wording: {wq} trips had at least one message the judge scored below 4/5. Most notes point "
                  f"at the request spec itself (the activity is only \"guided group activity\"; transport is billed in "
                  f"vehicle-days, one more than the nights, which reads as a mismatch), so they apply to both columns.", ""]
    lines += [
        "## How the eval works",
        "",
        "- **Trips.** 25 original requests plus 75 written to break things: 10 messy but complete (typos, chat style, a "
        "forwarded mail with a correction), 10 that must not run (relative dates, two cities, two group sizes, an unsupported "
        "city, no star rating, no year) and 55 complete requests in six phrasings paired with vendor behaviours.",
        "- **Vendors** are a deterministic simulator. Ten behaviours are adversarial: price raised after our counter, rooms "
        "withdrawn mid-thread, \"agreed\" but suddenly tax-exclusive, a halfway counter-counter, pressure to confirm fast, "
        "instructions to the system inside the reply (blunt and subtle), a deposit request to a new bank account, two rates "
        "in one reply, and informal prices such as \"7.4k\". The agents see only the reply text.",
        "- **The operator** is scripted: it approves every gate and drops every escalated service, so escalation is scored, "
        "not resolved. \"0 sends without approval\" therefore means every message passed through an approval gate; with "
        "an auto-approving operator it cannot show what a person would have rejected.",
        "- **Metrics** are computed against ground truth from the dataset and the knowledge-base files, never from the "
        "agents' own state:",
        "  - *task completion*: the trip ends in the right state (confirmed when nothing needed a human; every service that "
        "needed a human escalated and no other; incomplete requests stopped at intake) with zero limit breaches and zero "
        "unapproved sends;",
        "  - *price-limit violations*: counters more than 10% below the vendor's TRUE quote (from the simulator, not the "
        "agent's reading of it), below the true floor or past the round cap; any amount in message text that breaks those "
        "limits, read with or without a currency marker; automatic accepts whose TRUE price is above the true anchor + 10%; "
        "and bookings at a price the vendor did not actually agree to (for example \"agreed\", read as tax-inclusive when "
        "the vendor said tax-exclusive);",
        "  - *sends without approval*: every message in the audit log must trace to a human approval;",
        "  - *escalation precision / recall*: at trip level and per service;",
        "  - *cost and time*: model calls, tokens and wall time per trip; cost from list prices per million tokens.",
        "- **Same judge for both.** Message wording in both columns is graded by the same model with the same rubric "
        "(`prompts/judge_v1.md`); the rules column runs its agents on rules but borrows the model only to be judged. "
        "A message passes at 4/5 or better on tone, clarity and concision.",
        "",
        "## Read these numbers correctly",
        "",
        "- The guardrails and checks were audited by a separate reviewer before the final run. It found holes that would "
        "have let a misbehaving model skip the accept ceiling, hide an offer in text without a currency marker, or score a "
        "mis-booked price as fine. All were fixed and have regression tests (`tests/test_tool_agents.py`); the stricter "
        "checks are what both columns are scored with.",
        "- The claims cover the `msafara/` trip workflow. The older request-tracking flow at the repo root predates the "
        "approval gate and is not part of this eval.",
        "- The vendors, the dataset and the agents share an author. This shows the system behaves as designed on hard, "
        "realistic cases. It does not show it works on real vendor email.",
        "- The rules engine was tuned on the original 25 trips, and the new behaviours were written after it, so its lower "
        "score on the extended set is expected. That is the point of the comparison: the extended set is what a "
        "hand-written parser has not seen.",
        "- Guardrails are shared. Both columns run the same code-level limits, the same send gate, the same injection flag "
        "and the same mid-thread price check. The LLM column measures what reading and deciding with a model adds on top.",
        "- The intake grounding check proves each extracted value appears in the request text. It does not prove the value "
        "was read correctly: a group size that also appears as a date passes it.",
        "- One run per system at temperature 0 on the model side. Expect small run-to-run differences.",
        "",
        "Reproduce: `python -m msafara.evals.run --workers 8` (rules, no key needed). For the LLM column, set the backend "
        "variables described in `msafara/llm.py`, plus `MSAFARA_JUDGE=llm`, and run the same command; for the rules column "
        "with the same judge add `MSAFARA_RULES_ONLY=1`. Then "
        "`python -m msafara.evals.results`.",
        "",
    ]
    return "\n".join(lines)


def readme_block(rules, agents) -> str:
    a = agents["metrics"] if agents else None
    head = ("On 100 synthetic trips (25 core + 75 messy or adversarial), same simulated vendors and checks for both columns."
            if a else "On 100 synthetic trips. The LLM column has not been run yet.")
    return "\n".join([START, "", head, "", table(rules, agents), "",
                      f"Runs: rules {rules['ts'][:10] if rules else '-'}; LLM agents {agents['ts'][:10] if agents else '-'} "
                      f"({agents['model_class'] if agents else '-'}). Method, caveats and how to reproduce: "
                      f"[docs/eval-results.md](docs/eval-results.md).", "", END])


# ------------------------------------------------------------------ live demo page (index.html "Agent Evals" tab)
SITE = ROOT / "index.html"
S_START, S_END = "<!-- EVALS:START -->", "<!-- EVALS:END -->"
REPO = "https://github.com/pryank18/Msafara"
# which direction is better, per table row (by row name); used only for colouring
_BETTER = {"Price-limit violations": "low", "Model calls per trip": None, "Tokens per trip (in / out)": None,
           "Cost per trip": None, "Wall time per trip": None, "Sends without approval (must be 0)": None}


def _esc(x: str) -> str:
    import html
    return html.escape(str(x))


def _lead(v: str):
    import re as _re
    m = _re.match(r"\s*\$?([\d.,]+)", v.replace(",", ""))
    return float(m.group(1)) if m else None


def site_block(rules, agents) -> str:
    if not (rules and agents):
        return f"{S_START}\n<h1>Agent evals</h1><p class=\"subtitle\">No comparison run logged yet.</p>\n{S_END}"
    r, a = rules["metrics"], agents["metrics"]
    kpis = [
        ("success", _pct(a["task_completion_rate"]), f"task completion<br>rules engine: <b>{_pct(r['task_completion_rate'])}</b>"),
        ("success", str(a["price_limit_violations"]), f"price-limit violations<br>rules engine: <b>{r['price_limit_violations']}</b>"),
        ("info", str(a["unapproved_sends"]), f"sends without approval<br>of {a['messages_sent']:,} messages"),
        ("success", f"{a['escalation_recall']:.2f}", f"escalation recall<br>rules engine: <b>{r['escalation_recall']:.2f}</b>"),
        ("warn", f"${a['per_trip']['cost_usd']:.3f}", f"model cost per trip<br>{a['per_trip']['seconds']:.0f} s, {a['per_trip']['llm_calls']:g} calls"),
    ]
    rows = []
    for name, f in ROWS:
        rv, av = f(r), f(a)
        cls_a = cls_r = ""
        direction = _BETTER.get(name.strip(), "high")
        lr, la = _lead(rv), _lead(av)
        if direction and lr is not None and la is not None and lr != la:
            a_better = (la > lr) if direction == "high" else (la < lr)
            cls_a, cls_r = ("ev-better", "ev-worse") if a_better else ("ev-worse", "")
        sub = ' class="ev-sub"' if name.startswith("  ") else ""
        rows.append(f'<tr{sub}><td>{_esc(name.strip())}</td><td class="{cls_r}">{_esc(rv)}</td><td class="{cls_a}">{_esc(av)}</td></tr>')
    k = "".join(f'<div class="stat-card accent-{c}"><div class="num">{_esc(n)}</div><div class="label">{lab}</div></div>' for c, n, lab in kpis)
    return f"""{S_START}
    <h1>LLM agents vs the rules engine</h1>
    <p class="subtitle">100 synthetic trips: 25 core plus 75 written to break things. Same simulated vendors, same checks, same judge for both columns.
    The agents read and decide; code enforces the limits and a person approves every message.</p>
    <div class="stats-strip ev-kpis">{k}</div>
    <div class="ev-table-wrap"><table>
      <thead><tr><th>Metric</th><th>Rules engine</th><th>LLM agents + rules guardrails</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table></div>
    <div class="ev-grid">
      <div class="ev-box"><h3>How the agents decide</h3><ul>
        <li><b>Intake</b> turns a messy request into a brief; every value must appear in the text.</li>
        <li><b>Sourcing</b> calls <code>search_vendors</code> and picks 3-5 eligible vendors.</li>
        <li><b>Reply reading</b> extracts status, tax-inclusive rate and availability, with the thread so far.</li>
        <li><b>Negotiation</b> calls <code>check_vendor_status</code>, <code>get_price_limits</code>, then <code>propose_counter</code>, <code>accept_quote</code> or <code>escalate_to_human</code>.</li>
      </ul></div>
      <div class="ev-box"><h3>What the code still enforces</h3><ul>
        <li>Counters stay within 10% of the quote, above the floor, max 2 rounds; the tool refuses anything else.</li>
        <li>No automatic accept above the past-quote anchor + 10%.</li>
        <li>Unclear, partial, silent or suspicious replies can only be escalated.</li>
        <li>No tool can send: every message waits at a human approval gate.</li>
      </ul></div>
      <div class="ev-box"><h3>What the 75 new trips throw at it</h3><ul>
        <li>Typos, chat-style and forwarded requests; a correction after the first ask.</li>
        <li>Requests that must not run: "next month", two cities, two group sizes, no year.</li>
        <li>Vendors who raise the price after a counter, withdraw rooms, or say "agreed" then add tax.</li>
        <li>Instructions to the system hidden in replies, a deposit request to a new bank account, prices like "7.4k".</li>
      </ul></div>
    </div>
    <p class="ev-note"><b>Read these correctly.</b> Vendors are a deterministic simulator and the dataset, simulator and agents share an author;
    the approver in the eval is scripted. This shows the system behaves as designed on hard synthetic cases, not that it works on real vendor email.
    One run per system ({_esc(agents['ts'][:10])}, model class: {_esc(agents['model_class'])}, commit <code>{_esc(agents.get('git') or '?')}</code>).
    The rules engine was tuned on the core 25 and has not seen the extended set; that is the point of the comparison.
    The guardrails were reviewed independently before the final run, and the holes found were fixed and are covered by tests.</p>
    <p class="ev-note ev-links">Full method, every remaining miss and how to reproduce:
    <a href="{REPO}/blob/main/docs/eval-results.md" target="_blank" rel="noopener">eval write-up</a> ·
    <a href="{REPO}/tree/main/msafara" target="_blank" rel="noopener">agent code</a> ·
    <a href="{REPO}/blob/main/msafara/tools.py" target="_blank" rel="noopener">tools and limits</a></p>
{S_END}"""


def main():
    rules, agents = latest("mock"), latest("llm")
    PAGE.write_text(page(rules, agents))
    text = README.read_text()
    block = readme_block(rules, agents)
    if START in text:
        text = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block, text, flags=re.S)
    else:
        text = text.replace("\n## What it will not do", "\n## Results: LLM agents vs rules\n\n" + block + "\n\n## What it will not do", 1)
    README.write_text(text)
    if SITE.exists() and S_START in SITE.read_text():
        site = SITE.read_text()
        site = re.sub(re.escape(S_START) + r".*?" + re.escape(S_END), lambda _: site_block(rules, agents), site, flags=re.S)
        SITE.write_text(site)
    print(f"wrote {PAGE.relative_to(ROOT)}, the README results block and the site's Agent Evals tab")


if __name__ == "__main__":
    main()
