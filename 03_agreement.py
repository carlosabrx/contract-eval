"""
03_agreement.py — measure how much of your "model error" is actually label error

Your grader compares the model against CUAD's annotations and calls every mismatch a
model failure. That assumes the annotations are right. You already found four that
weren't, so that assumption is doing real work and has never been tested.

This script tests it. You label a sample yourself, BLIND to both CUAD's answer and the
model's, and then three-way agreement gets computed:

    you vs CUAD    -> label quality. This sets the CEILING: if you and the
                      annotators disagree 15% of the time, no model can
                      meaningfully score above ~85% against those labels.
    you vs model   -> the model's real accuracy, against a rater you trust
    CUAD vs model  -> what your grader has been reporting all along

The gap between the second and third numbers is how much of your reported error was
never the model's fault.

    python3 03_agreement.py --label              # label a sample, blind
    python3 03_agreement.py --label --disputes   # label only the contested docs
    python3 03_agreement.py --compare

Labeling 25 documents takes about 45 minutes. It is the most valuable 45 minutes in
the project and the part nobody else's portfolio has.
"""

import argparse
import csv
import random
import re
import sys
from math import comb
from pathlib import Path

FIELD = "change_of_control"
GOLD_COL = "gold_change_of_control"
HUMAN_FILE = "human_labels.csv"

# Same anchors the extractor uses, so you read what the model read.
ANCHORS = [r"change\s+(in|of)\s+control", r"merger|consolidation",
           r"assign(ment)?", r"successors?\s+and\s+assigns"]


def norm_bool(s):
    s = str(s or "").strip().lower()
    if s in ("true", "yes", "y", "1"):
        return "true"
    if s in ("false", "no", "n", "0"):
        return "false"
    return ""


def show_context(path, width=900, max_hits=6):
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        print(f"   (could not read {path}: {e})")
        return
    spans = []
    for pat in ANCHORS:
        for m in re.finditer(pat, text, re.IGNORECASE):
            spans.append((max(0, m.start() - width // 2),
                          min(len(text), m.end() + width // 2)))
    if not spans:
        print("   (no anchor matches — likely no change-of-control language)")
        return
    spans.sort()
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    for a, b in merged[:max_hits]:
        print("   " + "-" * 70)
        print("   " + re.sub(r"\s+", " ", text[a:b]).strip()[:800])
    if len(merged) > max_hits:
        print(f"   ... {len(merged) - max_hits} more passages — open the file if unsure")


def cohens_kappa(pairs):
    n = len(pairs)
    if n == 0:
        return None
    labels = sorted({x for p in pairs for x in p})
    po = sum(1 for a, b in pairs if a == b) / n
    pe = sum((sum(1 for a, _ in pairs if a == l) / n) *
             (sum(1 for _, b in pairs if b == l) / n) for l in labels)
    if pe >= 1.0:
        return 1.0 if po >= 1.0 else 0.0
    return (po - pe) / (1 - pe)


def load(gold_path, results_path):
    gold = {r["doc_id"]: r for r in csv.DictReader(open(gold_path, encoding="utf-8"))}
    res = {r["doc_id"]: r for r in csv.DictReader(open(results_path, encoding="utf-8"))}
    return gold, res


def cmd_label(args):
    gold, res = load(args.gold, args.results)
    docs = [d for d in gold if d in res]

    if args.disputes:
        docs = [d for d in docs
                if norm_bool(gold[d][GOLD_COL]) != norm_bool(res[d].get(FIELD))]
        print(f"Labeling only the {len(docs)} CONTESTED documents.\n"
              "NOTE: a disputes-only sample is biased and cannot be used for the\n"
              "ceiling kappa. Use it to adjudicate, not to measure.\n")
    else:
        random.seed(args.seed)
        docs = random.sample(docs, min(args.n, len(docs)))
        print(f"Labeling a RANDOM sample of {len(docs)} documents.\n")

    done = {}
    if Path(HUMAN_FILE).exists():
        done = {r["doc_id"]: r["human_label"]
                for r in csv.DictReader(open(HUMAN_FILE, encoding="utf-8"))}

    print("You are labeling BLIND. Neither CUAD's answer nor the model's is shown,")
    print("because seeing either would anchor you and destroy the measurement.\n")
    print("Question: does this contract give a party rights or remedies triggered by")
    print("a change in ownership or control of the other party?")
    print("A plain anti-assignment clause with no ownership trigger does NOT count.\n")
    print("  t = true    f = false    s = skip    q = save and quit\n")

    out = dict(done)
    for i, d in enumerate(docs, 1):
        if d in done:
            continue
        print("\n" + "=" * 74)
        print(f"[{i}/{len(docs)}] {d}")
        print(f"   file: {gold[d]['path']}")
        print("=" * 74)
        show_context(gold[d]["path"])
        while True:
            a = input("\n   your label [t/f/s/q]: ").strip().lower()
            if a in ("t", "f", "s", "q"):
                break
        if a == "q":
            break
        if a == "s":
            continue
        out[d] = {"t": "true", "f": "false"}[a]

    with open(HUMAN_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["doc_id", "human_label"])
        w.writeheader()
        for k, v in out.items():
            w.writerow({"doc_id": k, "human_label": v})
    print(f"\nWrote {HUMAN_FILE} ({len(out)} labels)")
    print("Next: python3 03_agreement.py --compare")


def cmd_compare(args):
    if not Path(HUMAN_FILE).exists():
        sys.exit(f"{HUMAN_FILE} not found — run --label first.")
    gold, res = load(args.gold, args.results)
    human = {r["doc_id"]: r["human_label"]
             for r in csv.DictReader(open(HUMAN_FILE, encoding="utf-8"))}

    shared = [d for d in human if d in gold and d in res]
    you   = {d: norm_bool(human[d]) for d in shared}
    cuad  = {d: norm_bool(gold[d][GOLD_COL]) for d in shared}
    model = {d: norm_bool(res[d].get(FIELD)) for d in shared}
    usable = [d for d in shared if you[d] and cuad[d] and model[d]]

    print(f"\n{'=' * 70}\nTHREE-WAY AGREEMENT — {FIELD}   (n={len(usable)})\n{'=' * 70}")

    # A rater who gave the same answer every time carries no information. Agreement
    # against them measures the constant, not the model. Refuse to report rather
    # than print a number that looks like a measurement and isn't.
    degenerate = []
    for name, d in [("you", you), ("CUAD", cuad), ("model", model)]:
        vals = {d[x] for x in usable}
        if len(vals) <= 1:
            degenerate.append((name, vals.pop() if vals else "(empty)"))
    if degenerate:
        for name, v in degenerate:
            n_lab = len(usable)
            print(f"\n  !! '{name}' answered '{v}' on all {n_lab} documents.")
        print("\n  Kappa is 0 by construction here, and every accuracy figure below")
        print("  would be measuring a constant. Nothing is reported.")
        print("\n  Two possibilities, and they need different responses:")
        print("   1. Labeling went too fast. Delete human_labels.csv and redo 12-15")
        print("      documents slowly.")
        print("   2. You applied a DIFFERENT DEFINITION than the annotators did.")
        print("      That is a finding, not an error: it means the label does not")
        print("      pin down a single construct, and no accuracy number computed")
        print("      against it means what it appears to mean.")
        print("\n  Either way: write the operational definition with edge-case rulings")
        print("  FIRST (spec Section 3), then re-label. Definition before labels.")
        print("\n  Raw distributions:")
        for name, d in [("you", you), ("CUAD", cuad), ("model", model)]:
            counts = {}
            for x in usable:
                counts[d[x]] = counts.get(d[x], 0) + 1
            print(f"    {name:6s} {counts}")
        return

    for name, a, b in [("you vs CUAD   (label quality / CEILING)", you, cuad),
                       ("you vs model  (real model accuracy)", you, model),
                       ("CUAD vs model (what your grader reports)", cuad, model)]:
        pairs = [(a[d], b[d]) for d in usable]
        agree = sum(1 for x, y in pairs if x == y)
        k = cohens_kappa(pairs)
        print(f"  {name:42s} {agree}/{len(pairs)} = {agree/len(pairs):5.1%}   k={k:.3f}")

    k_ceiling = cohens_kappa([(you[d], cuad[d]) for d in usable])
    if k_ceiling is not None and k_ceiling < 0.40:
        print(f"\n  !! you vs CUAD kappa is {k_ceiling:.3f} — POOR agreement.")
        print("     You and the annotators are not applying the same definition.")
        print("     Until that is resolved, accuracy against these labels measures")
        print("     definitional overlap, not extraction quality. Fix the definition")
        print("     before reporting any headline number.")

    ceiling = sum(1 for d in usable if you[d] == cuad[d]) / len(usable)
    print(f"\n  CEILING: you and CUAD agree {ceiling:.0%} of the time. No model can")
    print(f"           meaningfully exceed ~{ceiling:.0%} measured against these labels.")

    # The question this script exists to answer.
    disputes = [d for d in usable if cuad[d] != model[d]]
    model_right = [d for d in disputes if you[d] == model[d]]
    model_wrong = [d for d in disputes if you[d] == cuad[d]]
    unclear     = [d for d in disputes if d not in model_right and d not in model_wrong]

    print(f"\n{'=' * 70}\nADJUDICATING THE {len(disputes)} CASES YOUR GRADER CALLED MODEL ERRORS\n{'=' * 70}")
    print(f"  you sided with the MODEL (gold was wrong):  {len(model_right)}")
    print(f"  you sided with CUAD  (model was wrong):     {len(model_wrong)}")
    if unclear:
        print(f"  neither:                                    {len(unclear)}")
    if disputes:
        share = len(model_right) / len(disputes)
        print(f"\n  {share:.0%} of the failures your grader reported were label errors,")
        print(f"  not model errors. Corrected accuracy against your own labels:")
        corrected = sum(1 for d in usable if you[d] == model[d]) / len(usable)
        reported = sum(1 for d in usable if cuad[d] == model[d]) / len(usable)
        print(f"      reported (vs CUAD):    {reported:.1%}")
        print(f"      corrected (vs you):    {corrected:.1%}")

    for label, group in [("MODEL RIGHT, GOLD WRONG", model_right),
                         ("MODEL WRONG", model_wrong)]:
        if group:
            print(f"\n  {label}:")
            for d in group:
                print(f"    {d[:60]:60s} you={you[d]:5s} cuad={cuad[d]:5s} model={model[d]}")

    print("\n  Put the ceiling and the corrected accuracy in spec Section 7.")
    print("  Reporting an uncorrected number you know is wrong is the thing to avoid.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", action="store_true")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--disputes", action="store_true",
                    help="with --label: only the documents where CUAD and the model differ")
    ap.add_argument("--gold", default="gold.csv")
    ap.add_argument("--results", default="results_v2_excerpt.csv")
    ap.add_argument("--n", type=int, default=25)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

    if args.label:
        cmd_label(args)
    elif args.compare:
        cmd_compare(args)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
