"""
00_prepare.py — build a gold-label set from CUAD's master_clauses.csv

Reads the expert annotations, matches them to the plain-text contracts, and writes
gold.csv with one row per document.

Usage:
    python3 00_prepare.py                          # 30 random contracts >=15k chars
    python3 00_prepare.py --sample random --n 60   # 60 random contracts (real run)
    python3 00_prepare.py --require notice_period --n 40
                                                   # only sample documents that HAVE a
                                                   #   notice period — fixes sparse fields
    python3 00_prepare.py --min-chars 0            # no length floor (includes fragments)
    python3 00_prepare.py --audit                  # density + class balance of ALL 41
                                                   #   CUAD categories — use this to
                                                   #   pick fields worth evaluating
    python3 00_prepare.py --inspect                # just list the CSV columns and exit
"""

import argparse
import csv
import random
import re
import sys
from pathlib import Path

# Which CUAD annotation columns map to which of our fields.
# The values are prefixes — the script finds the real column by fuzzy match, because
# CUAD's exact column naming varies slightly between distributions.
# Chosen from `--audit` output against the >=15k-char pool:
#   Governing Law        375 examples, 71 distinct values   -> string normalization
#   Notice Period        91 examples, 20 distinct values    -> numeric (sparse: see --require)
#   Change Of Control    396 examples, 70/30 split          -> yes/no judgment
FIELDS = {
    "governing_law": "Governing Law",
    "notice_period": "Notice Period To Terminate Renewal",
    "change_of_control": "Change Of Control",
}

MASTER_CSV = Path("master_clauses.csv")
DOCS_DIR = Path("full_contract_txt")


def find_column(columns, prefix):
    """Find the CUAD column for a field, preferring the '-Answer' variant.

    CUAD stores two columns per category: the extracted span, and the normalized
    answer. We want the answer.
    """
    norm = lambda s: re.sub(r"[^a-z]", "", s.lower())
    target = norm(prefix)

    answer_cols, span_cols = [], []
    for col in columns:
        c = norm(col)
        if c.startswith(target):
            (answer_cols if c.endswith("answer") else span_cols).append(col)

    if answer_cols:
        return answer_cols[0]
    if span_cols:
        return span_cols[0]
    return None


def build_doc_index(docs_dir):
    """Map normalized filename stem -> Path, so CSV names match text files."""
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    index = {}
    for p in docs_dir.rglob("*.txt"):
        index[norm(p.stem)] = p
    return index, norm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30, help="how many documents")
    ap.add_argument("--sample", choices=["shortest", "random"], default="random",
                    help="'random' is the default: 'shortest' systematically selects "
                         "simpler contracts and produces degenerate class balance")
    ap.add_argument("--min-chars", type=int, default=15_000,
                    help="exclude documents shorter than this (CUAD contains cover "
                         "pages and stub exhibits that are not real contracts)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="gold.csv")
    ap.add_argument("--inspect", action="store_true", help="list columns and exit")
    ap.add_argument("--require", action="append", default=[], metavar="FIELD",
                    help="only sample documents where FIELD has a gold label. "
                         "Repeatable. Fixes sparse fields, but makes the sample "
                         "non-representative — see the warning it prints.")
    ap.add_argument("--audit", action="store_true",
                    help="report density and class balance for every CUAD category")
    args = ap.parse_args()

    if not MASTER_CSV.exists():
        sys.exit(f"ERROR: {MASTER_CSV} not found. Are you in the project folder?")
    if not DOCS_DIR.exists():
        sys.exit(f"ERROR: {DOCS_DIR}/ not found. Did you unzip CUAD into this folder?")

    with open(MASTER_CSV, encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames or []
        table = list(reader)
    print(f"Loaded {MASTER_CSV} — {len(table)} rows, {len(columns)} columns\n")

    if args.inspect:
        for c in columns:
            print("  ", c)
        return

    # --- resolve the filename column -------------------------------------------------
    fname_col = next(
        (c for c in columns if "filename" in c.lower() or c.lower() == "document name"),
        columns[0],
    )
    print(f"Using '{fname_col}' as the filename column.")

    # --- audit mode: which categories are actually worth evaluating? -----------------
    if args.audit:
        index, norm = build_doc_index(DOCS_DIR)
        pool = []
        for r in table:
            path = index.get(norm(Path(str(r.get(fname_col) or "")).stem))
            if path:
                try:
                    if path.stat().st_size >= args.min_chars:
                        pool.append(r)
                except OSError:
                    pass
        print(f"\nAuditing {len(pool)} documents >= {args.min_chars:,} chars\n")

        cats = [c for c in columns
                if re.sub(r"[^a-z]", "", c.lower()).endswith("answer")]
        report = []
        for col in cats:
            vals = []
            for r in pool:
                v = (r.get(col) or "").strip()
                if v and v.lower() not in ("nan", "none", "null"):
                    vals.append(v)
            if not vals:
                continue
            counts = {}
            for v in vals:
                counts[v[:32]] = counts.get(v[:32], 0) + 1
            top = sorted(counts.items(), key=lambda x: -x[1])
            report.append({
                "name": col.replace("-Answer", "").replace("- Answer", "").strip(),
                "n": len(vals),
                "pct": len(vals) / len(pool),
                "distinct": len(top),
                "skew": top[0][1] / len(vals),
                "top": top[0][0],
                "minority": len(vals) - top[0][1],
            })

        # Rank by how usable each category is as an eval target: enough examples,
        # and enough of the minority class to actually measure recall on it.
        report.sort(key=lambda x: -min(x["n"], 60) * (1 if x["distinct"] > 3
                                                      else min(x["minority"], 40) / 40))

        print(f"{'category':40s} {'n':>5s} {'fill':>6s} {'vals':>5s} {'skew':>6s} {'minority':>9s}")
        print("-" * 78)
        for r in report:
            flag = ""
            if r["distinct"] <= 3 and r["minority"] < 15:
                flag = "  <- too few minority cases"
            elif r["n"] < 20:
                flag = "  <- too sparse"
            print(f"{r['name'][:40]:40s} {r['n']:5d} {r['pct']:5.0%} "
                  f"{r['distinct']:5d} {r['skew']:5.0%} {r['minority']:9d}{flag}")

        print("\nReading this table:")
        print("  n         scorable examples available in the >= min-chars pool")
        print("  vals      distinct answers; 2-3 means a yes/no field")
        print("  skew      share held by the single most common answer")
        print("  minority  examples NOT in the most common class — the number that")
        print("            actually limits what you can measure on a yes/no field")
        print("\nGood eval targets: n >= 40, and for yes/no fields minority >= 15.")
        print("Update the FIELDS dict at the top of this file, then re-run.")
        return

    # --- resolve our three field columns ---------------------------------------------
    resolved = {}
    for field, prefix in FIELDS.items():
        col = find_column(columns, prefix)
        if col is None:
            print(f"  !! {field:22s} -> NO MATCH for '{prefix}'")
            print("     Run with --inspect to see the real column names, then edit FIELDS.")
        else:
            print(f"  ok {field:22s} -> '{col}'")
            resolved[field] = col

    if not resolved:
        sys.exit("\nERROR: no fields matched. Run --inspect and fix the FIELDS dict.")
    print()

    # --- match CSV rows to text files ------------------------------------------------
    index, norm = build_doc_index(DOCS_DIR)
    print(f"Found {len(index)} .txt files in {DOCS_DIR}/")

    rows = []
    unmatched = 0
    for r in table:
        raw_name = str(r.get(fname_col) or "")
        path = index.get(norm(Path(raw_name).stem))
        if path is None:
            unmatched += 1
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        row = {"doc_id": path.stem, "path": str(path), "char_count": size}
        for field, col in resolved.items():
            val = (r.get(col) or "").strip()
            row[f"gold_{field}"] = "" if val.lower() in ("nan", "none", "null") else val
        rows.append(row)

    print(f"Matched {len(rows)} documents ({unmatched} CSV rows had no matching .txt)\n")
    if not rows:
        sys.exit("ERROR: nothing matched. Check that full_contract_txt/ has the .txt files.")

    # --- length floor ----------------------------------------------------------------
    if args.min_chars:
        before = len(rows)
        rows = [r for r in rows if r["char_count"] >= args.min_chars]
        dropped = before - len(rows)
        if dropped:
            print(f"Excluded {dropped} documents under {args.min_chars:,} chars "
                  f"(cover pages, stub exhibits, truncated files)")
            print(f"  -> {len(rows)} candidate contracts remain\n")
        if not rows:
            sys.exit("ERROR: length floor excluded everything. Lower --min-chars.")

    # --- required-field filter (stratification) --------------------------------------
    for req in args.require:
        if req not in resolved:
            sys.exit(f"--require {req}: not a known field. Options: {list(resolved)}")
        before = len(rows)
        rows = [r for r in rows if r.get(f"gold_{req}", "").strip()]
        print(f"Required '{req}' to be labeled: {before} -> {len(rows)} documents")
        if not rows:
            sys.exit(f"ERROR: no documents have a {req} label.")
    if args.require:
        print()

    # --- sample ----------------------------------------------------------------------
    if args.sample == "shortest":
        rows.sort(key=lambda x: x["char_count"])
        picked = rows[: args.n]
        print(f"Sampling: {args.n} SHORTEST documents")
        print("  WARNING: shortest-first selects the simplest contracts in the corpus.")
        print("  They under-represent rare clauses. Use --sample random unless you")
        print("  have a specific reason, and record the choice in spec Section 4.")
    else:
        random.seed(args.seed)
        picked = random.sample(rows, min(args.n, len(rows)))
        print(f"Sampling: {args.n} RANDOM documents (seed={args.seed})")

    sizes = [p["char_count"] for p in picked]
    est_tokens = sum(sizes) / 4  # ~4 chars per token, rough
    print(f"\n  docs:            {len(picked)}")
    print(f"  size range:      {min(sizes):,} – {max(sizes):,} chars")
    print(f"  est input tokens: ~{est_tokens:,.0f} for one full pass")

    # --- gold coverage and class balance ----------------------------------------------
    print("\nGold label coverage and value distribution:")
    warnings = []
    for field in resolved:
        vals = [p.get(f"gold_{field}", "").strip() for p in picked]
        filled = [v for v in vals if v]
        print(f"\n  {field}")
        print(f"    populated: {len(filled)}/{len(picked)}")

        if not filled:
            warnings.append(f"{field}: NO labels at all — nothing to score against")
            continue

        counts = {}
        for v in filled:
            counts[v[:40]] = counts.get(v[:40], 0) + 1
        top = sorted(counts.items(), key=lambda x: -x[1])
        for val, n in top[:5]:
            print(f"      {n:3d}  {val}")
        if len(top) > 5:
            print(f"      ... {len(top) - 5} more distinct values")

        # Base rate: if one answer dominates, accuracy is a misleading metric, because
        # a model that always guesses that answer already scores the majority share.
        share = top[0][1] / len(filled)
        if len(top) <= 3 and share >= 0.75:
            warnings.append(
                f"{field}: {share:.0%} of labels are '{top[0][0]}' — always guessing "
                f"that scores {share:.0%} with zero understanding. Report balanced "
                f"accuracy or per-class recall, not raw accuracy.")
        if len(filled) < 10:
            warnings.append(
                f"{field}: only {len(filled)} scorable examples — too few to "
                f"distinguish a real difference from noise.")

    if warnings:
        print("\n" + "!" * 62)
        print("BEFORE YOU SPEND ANY API CALLS:")
        for w in warnings:
            print(f"  - {w}")
        print("!" * 62)

    if args.require:
        print("\nNOTE: this sample is conditioned on " +
              " and ".join(f"'{r}' being present" for r in args.require) + ".")
        print("      It is NOT representative of the corpus — documents with an")
        print("      auto-renewal notice period are a specific subgroup. Report")
        print("      per-field accuracy on this set, and do not present it as")
        print("      corpus-wide performance. Record this in spec Section 4.")

    fieldnames = ["doc_id", "path", "char_count"] + [f"gold_{f}" for f in resolved]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(picked)

    print(f"\nWrote {args.out} ({len(picked)} rows)")
    print("\nNext: open gold.csv in Excel and eyeball 5 rows. Do the labels look right?")
    print("Then run:  python3 01_extract.py --limit 3")


if __name__ == "__main__":
    main()
