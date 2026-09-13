"""
02_grade.py — score extractions against the gold labels

Usage:
    python3 02_grade.py results_v1.csv
    python3 02_grade.py results_v1.csv results_v2.csv    # side-by-side comparison

Writes graded_<name>.csv with a correct_<field> column per field.

IMPORTANT: your first accuracy number will be too low, and a large share of the
"errors" will be this grader being too strict rather than the model being wrong.
Fixing the normalizers below IS the work. Log every change in Section 9 of your spec.
"""

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa",
    "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan",
    "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island",
    "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont",
    "virginia", "washington", "west virginia", "wisconsin", "wyoming",
    "district of columbia",
}


_STATES_BY_LENGTH = sorted(US_STATES, key=lambda x: (-len(x), x))


def norm_jurisdiction(s):
    """Normalize a governing-law string to a bare jurisdiction name.

    THIS IS THE FUNCTION YOU WILL SPEND THE MOST TIME ON. Every rule you add here
    is a grading decision — add it to your spec's edge-case table.
    """
    if not s:
        return ""
    s = s.lower().strip().strip('."\'')
    # strip boilerplate wrappers
    s = re.sub(r"\b(the\s+)?(laws?|law)\s+of\s+", " ", s)
    s = re.sub(r"\b(the\s+)?(state|commonwealth|province)\s+of\s+", " ", s)
    # strip conflict-of-laws exclusions
    s = re.sub(r"\b(without\s+regard\s+to|excluding|irrespective\s+of).*$", "", s)
    s = re.sub(r"\b(conflicts?\s+of\s+laws?).*$", "", s)
    s = re.sub(r"\b(united\s+states|u\.?s\.?a?\.?)\b", "", s)
    s = re.sub(r"[^a-z\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Match longest name first, in a FIXED order. Two bugs live here if you don't:
    #   1. US_STATES is a set, so plain iteration order varies BETWEEN PROCESSES.
    #      A label naming two jurisdictions then normalizes differently on different
    #      runs, and your v1/v2 comparison is scored against two different answer keys.
    #   2. "west virginia" contains "virginia", so short-first matching mislabels it.
    for st in _STATES_BY_LENGTH:
        if re.search(rf"\b{re.escape(st)}\b", s):
            return st
    return s


AMBIGUOUS_DAYS = object()   # unitless small integer — cannot be scored


def norm_days(s):
    """Normalize a notice period to an integer number of days.

    Convention: 1 month = 30 days, 1 year = 365. That is a CHOICE, and it creates a
    collision: "12 months" -> 360 while "1 year" -> 365, so the same period scores as
    a mismatch depending on how the contract phrased it. See _days_equal below.
    """
    if not s:
        return None
    s = str(s).lower().strip()
    if s in ("none", "null", "n/a", "nan"):
        return None
    m = re.search(r"(\d+)\s*(day|week|month|year)", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return n * {"day": 1, "week": 7, "month": 30, "year": 365}[unit]
    if re.fullmatch(r"-?\d+(\.0)?", s):
        n = int(float(s))
        # A bare number under 13 almost certainly carries an implied unit that was
        # dropped from the label ("6" meaning six months). Scoring it as 6 DAYS
        # silently marks a correct answer wrong. Flag it for a manual ruling.
        return AMBIGUOUS_DAYS if 0 < n < 13 else n
    m = re.search(r"\d+", s)
    return int(m.group(0)) if m else None


def _days_equal(a, b):
    if a is AMBIGUOUS_DAYS or b is AMBIGUOUS_DAYS:
        return None          # excluded from scoring, reported separately
    if a is None or b is None:
        return a == b
    # 360 / 365 / 366 all mean "one year" under different conventions.
    year = {360, 365, 366}
    if a in year and b in year:
        return True
    return a == b


def norm_bool(s):
    if s is None:
        return None
    s = str(s).strip().lower()
    if s in ("true", "yes", "y", "1"):
        return True
    if s in ("false", "no", "n", "0"):
        return False
    return None


# field -> (normalizer, comparator)
GRADERS = {
    "governing_law": (
        norm_jurisdiction,
        lambda a, b: a == b or (bool(a) and bool(b) and (a in b or b in a)),
    ),
    "notice_period_days": (norm_days, _days_equal),
    "change_of_control": (norm_bool, lambda a, b: a == b),
}

# results column -> gold column
GOLD_MAP = {
    "governing_law": "gold_governing_law",
    "notice_period_days": "gold_notice_period",
    "change_of_control": "gold_change_of_control",
}


def grade(results_path, gold_path="gold.csv", null_as_false=False):
    if not Path(results_path).exists():
        sys.exit(f"{results_path} not found.")
    with open(gold_path, encoding="utf-8") as f:
        gold = {r["doc_id"]: r for r in csv.DictReader(f)}
    with open(results_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    stats = {f: {"correct": 0, "scored": 0, "skipped_no_gold": 0} for f in GRADERS}
    statuses = Counter()
    out_rows = []

    for r in rows:
        statuses[r.get("parse_status", "")] += 1
        g = gold.get(r["doc_id"])
        out = dict(r)
        if not g:
            out_rows.append(out)
            continue

        for field, (normalize, equal) in GRADERS.items():
            gold_raw = g.get(GOLD_MAP[field], "")
            # Blank gold means the annotators found no such clause. Scoring those
            # requires deciding whether abstention counts as correct — see Section 2
            # of the spec. Until you decide, they're excluded.
            if not str(gold_raw).strip():
                stats[field]["skipped_no_gold"] += 1
                out[f"correct_{field}"] = ""
                continue
            pred_n = normalize(r.get(field, ""))
            if null_as_false and pred_n is None and normalize.__name__ == "norm_bool":
                pred_n = False
            gold_n = normalize(gold_raw)
            ok = equal(pred_n, gold_n)
            if ok is None:
                stats[field]["ambiguous"] = stats[field].get("ambiguous", 0) + 1
                out[f"correct_{field}"] = "AMBIGUOUS"
                out[f"norm_pred_{field}"] = "ambiguous"
                out[f"norm_gold_{field}"] = "ambiguous"
                continue
            stats[field]["scored"] += 1
            stats[field]["correct"] += int(ok)
            stats[field].setdefault("by_class", {})
            cls = stats[field]["by_class"].setdefault(str(gold_n), [0, 0])
            cls[1] += 1
            cls[0] += int(ok)
            out[f"correct_{field}"] = ok
            out[f"norm_pred_{field}"] = pred_n
            out[f"norm_gold_{field}"] = gold_n
        out_rows.append(out)

    name = Path(results_path).stem
    out_path = f"graded_{name}.csv"
    if out_rows:
        keys = []
        for r in out_rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(out_rows)

    return stats, statuses, out_path, len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="+")
    ap.add_argument("--gold", default="gold.csv")
    ap.add_argument("--null-as-false", action="store_true",
                    help="score a null/blank prediction on a yes-no field as False. "
                         "This is a SPEC DECISION (Section 2), not a bug fix: it says "
                         "abstention on an absent clause counts as correctly reporting "
                         "absence. Report which ruling you used.")
    ap.add_argument("--failures", metavar="FIELD",
                    help="print every failure for FIELD, with normalized values, so "
                         "you can separate grader bugs from real model errors")
    args = ap.parse_args()

    if args.failures:
        import itertools
        for path in args.results:
            grade(path, args.gold, args.null_as_false)
            with open(f"graded_{Path(path).stem}.csv", encoding="utf-8") as f:
                rows = [r for r in csv.DictReader(f)
                        if r.get(f"correct_{args.failures}") == "False"]
            print(f"\n{len(rows)} failures on '{args.failures}' in {path}")
            print("=" * 78)
            for r in rows:
                print(f"\n{r['doc_id'][:70]}")
                print(f"  gold : {r.get(f'norm_gold_{args.failures}','')!r}"
                      f"   <- raw: {r.get(args.failures,'')[:60]!r}")
                print(f"  model: {r.get(f'norm_pred_{args.failures}','')!r}")
                if r.get("n_windows"):
                    print(f"  retrieval: {r['n_windows']} windows, "
                          f"{r.get('input_chars','?')} of {r.get('orig_chars','?')} chars")
            print("\n" + "=" * 78)
            print("For each: is the MODEL wrong, or is the GRADER wrong?")
            print("  grader wrong -> fix the normalizer in this file, log it in spec S9")
            print("  model wrong  -> name the failure mode, count it, that's your table")
        return

    all_stats = {}
    for path in args.results:
        stats, statuses, out_path, n = grade(path, args.gold, args.null_as_false)
        all_stats[path] = stats
        print(f"\n{'=' * 62}")
        print(f"{path}   ({n} rows)"
              + ("   [null scored as False]" if args.null_as_false else ""))
        print("=" * 62)
        for field, s in stats.items():
            if s["scored"]:
                acc = s["correct"] / s["scored"]
                bar = "█" * int(acc * 28)
                print(f"  {field:22s} {acc:6.1%}  {s['correct']:3d}/{s['scored']:<3d} {bar}")
            else:
                print(f"  {field:22s}    n/a  (no gold labels)")
            by_class = s.get("by_class", {})
            if 1 < len(by_class) <= 4:
                # On an imbalanced field, overall accuracy is dominated by the
                # majority class. Per-class recall is the honest view.
                parts = [f"{k}: {c}/{n} ({c/n:.0%})" for k, (c, n) in
                         sorted(by_class.items(), key=lambda x: -x[1][1])]
                print(f"  {'':22s}         recall by class -> " + "   ".join(parts))
            if s.get("ambiguous"):
                print(f"  {'':22s}         {s['ambiguous']} excluded: gold label has no "
                      f"unit (e.g. '6') — needs a manual ruling, spec S3")
            if s["skipped_no_gold"]:
                print(f"  {'':22s}         {s['skipped_no_gold']} excluded (gold blank)")
        odd = {k: v for k, v in statuses.items() if k not in ("ok", "")}
        if odd:
            print(f"\n  non-clean statuses: {dict(odd)}")
        print(f"\n  wrote {out_path}")

    if len(args.results) > 1:
        print(f"\n{'=' * 62}")
        print("COMPARISON")
        print("=" * 62)
        fields = list(GRADERS)
        header = f"  {'field':22s}" + "".join(f"{Path(p).stem:>14s}" for p in args.results)
        print(header)
        for field in fields:
            line = f"  {field:22s}"
            accs = []
            for p in args.results:
                s = all_stats[p][field]
                a = s["correct"] / s["scored"] if s["scored"] else None
                accs.append(a)
                line += f"{(f'{a:.1%}' if a is not None else 'n/a'):>14s}"
            if len(accs) == 2 and None not in accs:
                line += f"   Δ {accs[1] - accs[0]:+.1%}"
            print(line)

    print("\nNext: open the graded CSV, filter correct_* = False, and read every failure.")
    print("Name 4–6 failure modes and count them. That table is your write-up.")


if __name__ == "__main__":
    main()
