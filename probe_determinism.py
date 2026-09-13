"""
probe_determinism.py — does this model return the same answer twice?

You concluded Gemini 3 ignores temperature and seed, so self-consistency is
unmeasurable. That follows only if the model is DETERMINISTIC. Ignoring your
temperature request and being deterministic are different things: a model can
vary run to run in a way you cannot steer.

If repeated identical calls differ, then:
  - self-consistency IS measurable (you just can't tune the spread), and
  - your single-run accuracy numbers carry run-to-run variance you have
    not measured, which affects every delta you reported last week.

    python3 probe_determinism.py                 # 10 docs x 3 calls on lite
    python3 probe_determinism.py --model flash-25 --n 6
"""

import argparse
import csv
import importlib.util
from collections import Counter
from pathlib import Path

import llm

_s = importlib.util.spec_from_file_location("extract", Path(__file__).parent / "01_extract.py")
extract = importlib.util.module_from_spec(_s)
_s.loader.exec_module(extract)

FIELDS = extract.FIELDS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="gold.csv")
    ap.add_argument("--model", default="lite")
    ap.add_argument("--n", type=int, default=10, help="documents to probe")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--temperature", type=float, default=1.0)
    args = ap.parse_args()

    docs = list(csv.DictReader(open(args.gold, encoding="utf-8")))[: args.n]
    provider, model_id = llm.resolve(args.model)
    print(f"model:       {model_id}")
    print(f"temperature: {args.temperature} (may be ignored — that's the point)")
    print(f"probing:     {len(docs)} docs x {args.repeats} identical calls "
          f"= {len(docs) * args.repeats} calls\n")

    template = extract.PROMPTS["v2"]
    varied_docs = 0
    field_variation = Counter()

    for i, d in enumerate(docs, 1):
        text, _ = extract.excerpt(Path(d["path"]).read_text(encoding="utf-8", errors="ignore"))
        prompt = template.replace("{document}", text)

        answers = []
        for rep in range(args.repeats):
            try:
                raw, _, _ = llm.call(prompt, model=args.model, max_tokens=1000,
                                     temperature=args.temperature, seed=rep)
                parsed, _ = extract.parse_json(raw)
                answers.append(tuple(str(parsed.get(f, "")) for f in FIELDS)
                               if parsed else ("PARSE_FAIL",) * len(FIELDS))
            except Exception as e:
                print(f"  call failed: {str(e)[:100]}")
                answers.append(("ERROR",) * len(FIELDS))

        distinct = len(set(answers))
        if distinct > 1:
            varied_docs += 1
            for j, f in enumerate(FIELDS):
                if len({a[j] for a in answers}) > 1:
                    field_variation[f] += 1

        mark = "VARIED" if distinct > 1 else "same  "
        print(f"[{i}/{len(docs)}] {mark}  {d['doc_id'][:42]:42s} "
              f"{distinct} distinct / {args.repeats}")
        if distinct > 1:
            for j, f in enumerate(FIELDS):
                vals = [a[j] for a in answers]
                if len(set(vals)) > 1:
                    print(f"           {f}: {vals}")

    print(f"\n{'=' * 66}")
    print(f"  {varied_docs}/{len(docs)} documents produced different answers "
          f"across identical calls")
    if field_variation:
        print(f"  variation by field: {dict(field_variation)}")

    if varied_docs == 0:
        print("""
  DETERMINISTIC. Self-consistency is genuinely unmeasurable on this model,
  and your single-run numbers are stable. Report Signal A only, and note
  that the API gave you no way to get a second opinion out of the model.

  If you still want Signal B, gemini-2.5-flash honors temperature:
      python3 probe_determinism.py --model flash-25
  Run BOTH signals on whichever model you choose — mixing models across
  arms makes the comparison meaningless.""")
    else:
        print(f"""
  NONDETERMINISTIC. Two things follow.

  1. Self-consistency IS measurable. You cannot tune the spread, but you can
     observe it, and observed disagreement is a confidence signal. Remove the
     k>1 guard in 04_sample.py and run k=5.

  2. Your single-run accuracy numbers carry run-to-run variance. {varied_docs} of
     {len(docs)} documents moved on identical input. Any delta you reported
     smaller than that variance is not attributable to the intervention.
     This belongs in Limitations, and it strengthens the McNemar framing:
     paired per-document tests survive this; raw accuracy deltas do not.""")


if __name__ == "__main__":
    main()
