"""
04_sample.py — collect k samples per document for self-consistency confidence.

Gemini 3 ignores temperature/seed/candidateCount, but it is not deterministic:
identical calls can disagree. That disagreement is Signal B. You cannot tune
the spread; you can only observe it.

Resumable on (doc_id, sample_idx).

Usage:
    python3 04_sample.py --k 5 --model lite
"""

import argparse
import csv
import importlib.util
import sys
import time
from pathlib import Path

import llm

_spec = importlib.util.spec_from_file_location("extract", Path(__file__).parent / "01_extract.py")
extract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(extract)

# v2 unchanged except this field appended. Self-reported confidence is one extra
# key on the same JSON object — not a separate call, not a prompt rewrite.
CONFIDENCE_ADDON = """
Also include this field in the same JSON object:
  "confidence": <integer 0-100, how confident you are in the above answers>
"""

FIELDS = extract.FIELDS


def load_done(path):
    done = set()
    if not Path(path).exists():
        return done
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            done.add((r["doc_id"], int(r["sample_idx"])))
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="gold.csv")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--model", default="lite")
    ap.add_argument("--out", default="samples.csv")
    args = ap.parse_args()

    if not Path(args.gold).exists():
        sys.exit(f"{args.gold} not found. Run 00_prepare.py first.")

    with open(args.gold, encoding="utf-8") as f:
        docs = list(csv.DictReader(f))

    done = load_done(args.out)
    jobs = [(d, i) for d in docs for i in range(args.k)
            if (d["doc_id"], i) not in done]

    provider, model_id = llm.resolve(args.model)
    print(f"model:       {model_id}  ({provider})")
    print(f"prompt:      v2 + confidence field")
    print(f"temperature: {args.temperature}  (Gemini 3 may ignore this; variation is run-to-run)")
    print(f"k:           {args.k}")
    print(f"output:      {args.out}")
    print(f"jobs:        {len(jobs)} to process "
          f"({len(done)} already done, skipping)\n")
    if not jobs:
        print("Nothing to do. Delete the output file to force a re-run.")
        return

    template = extract.PROMPTS["v2"] + CONFIDENCE_ADDON
    cols = ["doc_id", "sample_idx"] + FIELDS + [
        "self_confidence", "temperature", "model",
        "parse_status", "latency_s", "input_tokens", "output_tokens",
        "raw_response",
    ]
    new_file = not Path(args.out).exists()
    fh = open(args.out, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(fh, fieldnames=cols)
    if new_file:
        writer.writeheader()

    n_parse_fail = 0
    tot_in = tot_out = 0

    for n, (d, sample_idx) in enumerate(jobs, 1):
        try:
            text = Path(d["path"]).read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            print(f"[{n}/{len(jobs)}] {d['doc_id'][:40]:40s} "
                  f"s{sample_idx} SKIP ({e})")
            continue

        text, _ = extract.excerpt(text)
        prompt = template.replace("{document}", text)

        t0 = time.time()
        raw, err, in_tok, out_tok = None, None, 0, 0
        try:
            raw, in_tok, out_tok = llm.call(
                prompt, model=args.model, max_tokens=1000,
                temperature=args.temperature,
            )
            tot_in += in_tok
            tot_out += out_tok
        except Exception as e:
            err = str(e)

        elapsed = time.time() - t0

        # API errors are NOT written, so resume retries them.
        if raw is None:
            print(f"[{n}/{len(jobs)}] {d['doc_id'][:40]:40s} "
                  f"s{sample_idx} API ERROR")
            continue

        parsed, status = extract.parse_json(raw)
        if parsed is None:
            parsed, n_parse_fail = {}, n_parse_fail + 1

        conf = parsed.get("confidence", "")
        row = {
            "doc_id": d["doc_id"],
            "sample_idx": sample_idx,
            "self_confidence": "" if conf is None else str(conf),
            "temperature": args.temperature,
            "model": args.model,
            "parse_status": status or "ok",
            "latency_s": round(elapsed, 2),
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "raw_response": raw[:2000],
        }
        for f_ in FIELDS:
            v = parsed.get(f_)
            row[f_] = "" if v is None else str(v)
        writer.writerow(row)
        fh.flush()

        flag = "" if (status or "ok") == "ok" else f"  <{status}>"
        print(f"[{n}/{len(jobs)}] {d['doc_id'][:40]:40s} "
              f"s{sample_idx}/{args.k - 1}  "
              f"{str(row['governing_law'])[:16]:16s} "
              f"conf={str(row['self_confidence']):>3s}  "
              f"{elapsed:4.1f}s{flag}")

    fh.close()
    print(f"\n{'—' * 60}")
    print(f"  input tokens:   {tot_in:,}")
    print(f"  output tokens:  {tot_out:,}")
    if n_parse_fail:
        print(f"  parse failures: {n_parse_fail}")
    print(f"\nWrote {args.out}")
    print("Next:  python3 05_calibrate.py samples.csv --field governing_law")


if __name__ == "__main__":
    main()
