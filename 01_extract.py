"""
01_extract.py — run clause extraction over the document set

Reads gold.csv, sends each document to Claude, saves parsed answers to results_<v>.csv.

Resumable: if it crashes or you Ctrl-C it, re-running skips documents already done.
You never pay twice for the same extraction.

Usage:
    python3 01_extract.py --limit 3                 # smoke test, a few cents
    python3 01_extract.py                           # full run, prompt v1
    python3 01_extract.py --prompt-version v2       # full run, prompt v2
    python3 01_extract.py --model sonnet            # swap provider entirely

Models: gemini-flash (free, default), gemini-flash-lite, sonnet, haiku, opus
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

import llm


# ---------------------------------------------------------------------------------
# PROMPTS
#
# Keep every version. Never edit v1 after you have results from it — add v2 instead.
# The whole point of the experiment is comparing versions, which requires v1 to stay
# exactly as it was when you ran it.
# ---------------------------------------------------------------------------------

PROMPTS = {
    "v1": """You are extracting specific terms from a commercial contract.

Extract these three fields and return ONLY a JSON object, no other text:

{
  "governing_law": "<US state or country whose law governs, or null>",
  "notice_period_days": <integer days of notice required to terminate/non-renew, or null>,
  "change_of_control": <true if the contract gives a party rights or remedies
                        triggered by a change of control of the other party,
                        false if it does not, null if you cannot tell>
}

Return null for any field not present in the contract. Do not guess.

CONTRACT:
{document}
""",

    # v2 — TARGET ONE FAILURE MODE. Copy v1, change one thing, write down what and why.
    "v2": """You are extracting specific terms from a commercial contract.

Extract these three fields and return ONLY a JSON object, no other text:

{
  "governing_law": "<US state or country whose law governs, or null>",
  "notice_period_days": <integer days of notice required to terminate/non-renew, or null>,
  "change_of_control": <true if the contract gives a party rights or remedies
                        triggered by a change of control of the other party,
                        false if it does not, null if you cannot tell>
}

Rules:
- governing_law: return the bare jurisdiction name only. "Delaware", not "the laws of
  the State of Delaware". Ignore conflict-of-laws exclusions.
- notice_period_days: convert months to days at 30 days per month. If notice differs
  for termination-for-cause vs for-convenience, return the for-convenience period.
- change_of_control: a termination right, consent requirement, or assignment
  restriction triggered by a merger, acquisition, or transfer of ownership all count.
  A general anti-assignment clause with no ownership-change trigger does NOT count.
- change_of_control and other yes/no fields: you MUST answer true or false. "The
  contract contains no such provision" is false, NOT null. Use null ONLY if the text
  you were given is too fragmentary to judge at all.
- For value fields (governing_law, notice_period_days), return null rather than
  guessing. A null is better than a wrong value.

CONTRACT:
{document}
""",
}

FIELDS = ["governing_law", "notice_period_days", "change_of_control"]



# Keyword anchors per field. A hit pulls a window of surrounding text.
# This is a deliberately dumb retriever — the point is to show that a cheap
# keyword slice recovers most of the signal at a fraction of the tokens.
# Anchors grouped by the field they serve. Grouping matters: filling the budget in
# document order starves whichever clause sits latest in the contract, and governing
# law almost always sits last (the closing "Miscellaneous" section). v1 of this
# function did exactly that and silently truncated away the governing law clause on
# every contract over ~120k chars.
ANCHOR_GROUPS = {
    "law": [r"governing\s+law", r"shall\s+be\s+governed", r"laws?\s+of\s+the\s+State",
            r"construed\s+in\s+accordance", r"jurisdiction\s+of\s+the\s+courts"],
    "notice": [r"notice\s+of\s+(non-?renewal|termination)", r"terminate\s+this\s+Agreement",
               r"auto(matically)?\s+renew", r"renewal\s+term",
               r"days?[\u2019']?\s+(prior\s+)?written\s+notice",
               r"months?[\u2019']?\s+(prior\s+)?written\s+notice"],
    "coc": [r"change\s+(in|of)\s+control", r"merger|consolidation",
            r"assign(ment)?", r"successors?\s+and\s+assigns"],
}


def excerpt(text, window=1800, max_chars=36_000):
    """Pull windows of text around clause keywords instead of sending everything.

    Budget is allocated ROUND-ROBIN across anchor groups, so no field is starved by
    another field's matches. Returns (excerpt_text, n_windows).
    """
    def windows_for(pats):
        spans = []
        for pat in pats:
            for m in re.finditer(pat, text, re.IGNORECASE):
                spans.append((max(0, m.start() - window // 2),
                              min(len(text), m.end() + window // 2)))
        if not spans:
            return []
        spans.sort()
        merged = [list(spans[0])]
        for a, b in spans[1:]:
            if a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        return merged

    per_group = {g: windows_for(pats) for g, pats in ANCHOR_GROUPS.items()}
    if not any(per_group.values()):
        return text[:8000] + "\n[...]\n" + text[-6000:], 0

    # Round-robin: take one window from each group in turn until the budget is spent.
    chosen, total = [], 0
    idx = {g: 0 for g in per_group}
    while total < max_chars:
        progressed = False
        for g, wins in per_group.items():
            if idx[g] >= len(wins):
                continue
            a, b = wins[idx[g]]
            idx[g] += 1
            progressed = True
            if total + (b - a) > max_chars:
                continue
            chosen.append((a, b))
            total += b - a
        if not progressed:
            break

    # Governing law and the closing sections live at the end. Always keep the tail.
    tail_start = max(0, len(text) - 4000)
    if not any(a <= tail_start < b for a, b in chosen):
        chosen.append((tail_start, len(text)))

    chosen.sort()
    return "\n[...]\n".join(text[a:b] for a, b in chosen), len(chosen)


def parse_json(text):
    """Pull a JSON object out of the model's reply.

    Parse failures are a real failure mode worth counting, not an annoyance to
    paper over — so this returns the error rather than silently returning empty.
    """
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t), None
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0)), "recovered_from_prose"
            except json.JSONDecodeError:
                pass
        return None, "parse_failure"


def load_done(path):
    if not Path(path).exists():
        return set()
    with open(path, encoding="utf-8") as f:
        return {r["doc_id"] for r in csv.DictReader(f)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="gold.csv")
    ap.add_argument("--prompt-version", default="v1", choices=list(PROMPTS))
    ap.add_argument("--model", default=llm.DEFAULT_MODEL)
    ap.add_argument("--limit", type=int, help="only process N docs (smoke test)")
    ap.add_argument("--max-chars", type=int, default=120_000,
                    help="truncate documents longer than this")
    ap.add_argument("--excerpt", action="store_true",
                    help="send keyword-anchored excerpts instead of whole documents. "
                         "Cuts tokens ~10-20x. Record this as a config in your spec — "
                         "whole-doc vs excerpt is a real experiment, not a detail.")
    ap.add_argument("--out")
    args = ap.parse_args()

    out_path = args.out or (f"results_{args.prompt_version}"
                            f"{'_excerpt' if args.excerpt else ''}.csv")

    if not Path(args.gold).exists():
        sys.exit(f"{args.gold} not found. Run 00_prepare.py first.")

    template = PROMPTS[args.prompt_version]

    with open(args.gold, encoding="utf-8") as f:
        docs = list(csv.DictReader(f))
    if args.limit:
        docs = docs[: args.limit]

    done = load_done(out_path)
    todo = [d for d in docs if d["doc_id"] not in done]

    provider, model_id = llm.resolve(args.model)
    print(f"model:   {model_id}  ({provider})")
    print(f"prompt:  {args.prompt_version}")
    print(f"input:   {'keyword excerpts' if args.excerpt else 'whole documents'}")
    print(f"output:  {out_path}")
    print(f"docs:    {len(todo)} to process ({len(done)} already done, skipping)\n")
    if not todo:
        print("Nothing to do. Delete the output file to force a re-run.")
        return

    cols = ["doc_id", "prompt_version", "model"] + FIELDS + [
        "parse_status", "latency_s", "input_tokens", "output_tokens",
        "input_chars", "orig_chars", "n_windows", "raw_response"
    ]
    new_file = not Path(out_path).exists()
    fh = open(out_path, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(fh, fieldnames=cols)
    if new_file:
        writer.writeheader()

    tot_in = tot_out = 0
    latencies = []
    n_parse_fail = 0

    for i, d in enumerate(todo, 1):
        try:
            text = Path(d["path"]).read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            print(f"[{i}/{len(todo)}] {d['doc_id'][:45]:45s} SKIP ({e})")
            continue

        orig_chars = len(text)
        n_windows = None
        if args.excerpt:
            text, n_windows = excerpt(text)
        truncated = len(text) > args.max_chars
        if truncated:
            text = text[: args.max_chars]

        prompt = template.replace("{document}", text)

        t0 = time.time()
        raw, err, in_tok, out_tok = None, None, 0, 0
        try:
            raw, in_tok, out_tok = llm.call(prompt, model=args.model, max_tokens=1000)
            tot_in += in_tok
            tot_out += out_tok
        except Exception as e:
            err = str(e)

        elapsed = time.time() - t0
        latencies.append(elapsed)

        if raw is None:
            print(f"[{i}/{len(todo)}] {d['doc_id'][:45]:45s} API ERROR")
            writer.writerow({"doc_id": d["doc_id"], "prompt_version": args.prompt_version,
                             "model": args.model, "parse_status": "api_error",
                             "latency_s": round(elapsed, 2), "raw_response": (err or "")[:500]})
            fh.flush()
            continue

        parsed, status = parse_json(raw)
        if parsed is None:
            parsed, n_parse_fail = {}, n_parse_fail + 1

        row = {
            "doc_id": d["doc_id"],
            "prompt_version": args.prompt_version,
            "model": args.model,
            "parse_status": status or ("truncated_input" if truncated else "ok"),
            "input_chars": len(text),
            "orig_chars": orig_chars,
            "n_windows": n_windows if n_windows is not None else "",
            "latency_s": round(elapsed, 2),
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "raw_response": raw[:2000],
        }
        for f_ in FIELDS:
            v = parsed.get(f_)
            row[f_] = "" if v is None else str(v)
        writer.writerow(row)
        fh.flush()  # write as we go, so a crash loses nothing

        flag = "" if status in (None, "ok") else f"  <{status}>"
        print(f"[{i}/{len(todo)}] {d['doc_id'][:45]:45s} "
              f"{str(row['governing_law'])[:18]:18s} {elapsed:5.1f}s{flag}")

    fh.close()

    print(f"\n{'—' * 60}")
    print(f"  input tokens:   {tot_in:,}")
    print(f"  output tokens:  {tot_out:,}")
    cost, note = llm.cost_estimate(args.model, tot_in, tot_out)
    if cost is not None:
        tag = f"  ({note})" if note else "  (rough — verify current rates)"
        print(f"  est. cost:      ${cost:.2f}{tag}")
    if latencies:
        latencies.sort()
        p95 = latencies[min(int(len(latencies) * 0.95), len(latencies) - 1)]
        print(f"  median latency: {latencies[len(latencies)//2]:.1f}s")
        print(f"  p95 latency:    {p95:.1f}s")
    if n_parse_fail:
        print(f"  parse failures: {n_parse_fail}  <- count this as its own failure mode")
    print(f"\nWrote {out_path}")
    print(f"Next:  python3 02_grade.py {out_path}")


if __name__ == "__main__":
    main()
