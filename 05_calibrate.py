"""
05_calibrate.py — coverage–accuracy, ECE, operating points.

Usage:
    python3 05_calibrate.py samples.csv --field governing_law
    python3 05_calibrate.py samples.csv --all

Writes calibration.json and review_ui.html (JSON inlined — no fetch()).
"""

import argparse
import csv
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

_HERE = Path(__file__).parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


grade_mod = _load("grade", "02_grade.py")
extract = _load("extract", "01_extract.py")

GRADERS = grade_mod.GRADERS
GOLD_MAP = grade_mod.GOLD_MAP
FIELDS = list(GRADERS)

FIELD_GROUP = {
    "governing_law": "law",
    "notice_period_days": "notice",
    "change_of_control": "coc",
}

BINS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]
ACC_TARGETS = [0.90, 0.95, 0.99]


def _norm_key(v):
    """Hashable form of a CSV cell. Empty / null / None are the same answer."""
    if v is None:
        return ""
    s = str(v).strip()
    if s.lower() in ("none", "null", "nan"):
        return ""
    return s


def modal(values):
    """First-by-sample-order on ties. Spec §8: 2/2/1 → first, consistency 0.4."""
    keys = [_norm_key(v) for v in values]
    counts = Counter(keys)
    best = max(counts.values())
    for k in keys:
        if counts[k] == best:
            return k, counts[k] / len(keys)
    return keys[0], 1.0


def parse_conf(raw):
    if raw is None or str(raw).strip() == "":
        return None
    try:
        n = float(raw)
    except ValueError:
        return None
    if n > 1:
        n = n / 100.0
    return max(0.0, min(1.0, n))


def score(field, pred, gold_raw, null_as_false=True):
    """Return (correct: bool|None, pred_n, gold_n). None = unscored."""
    if not str(gold_raw or "").strip():
        return None, None, None
    normalize, equal = GRADERS[field]
    pred_n = normalize(pred)
    if null_as_false and pred_n is None and getattr(normalize, "__name__", "") == "norm_bool":
        pred_n = False
    gold_n = normalize(gold_raw)
    ok = equal(pred_n, gold_n)
    if ok is None:
        return None, pred_n, gold_n
    def jsonable(x):
        if x is grade_mod.AMBIGUOUS_DAYS:
            return "ambiguous"
        if isinstance(x, (bool, int, float)) or x is None:
            return x
        return str(x)
    return bool(ok), jsonable(pred_n), jsonable(gold_n)


def passage_for(field, path, pred, window=700):
    """Short source window the reviewer should read — not the whole contract."""
    p = Path(path)
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8", errors="ignore")
    if not text:
        return ""

    hits = []
    pred_s = _norm_key(pred)
    if len(pred_s) >= 4 and pred_s.lower() not in ("true", "false"):
        for m in re.finditer(re.escape(pred_s), text, re.IGNORECASE):
            hits.append(m.start())

    for pat in extract.ANCHOR_GROUPS.get(FIELD_GROUP[field], []):
        for m in re.finditer(pat, text, re.IGNORECASE):
            hits.append(m.start())

    if not hits:
        tail = text[-4000:]
        return tail[:1200] + ("…" if len(tail) > 1200 else "")

    # Prefer a hit whose window also contains the predicted string, else first.
    hits.sort()
    chosen = hits[0]
    if pred_s and len(pred_s) >= 4:
        for h in hits:
            lo, hi = max(0, h - window // 2), min(len(text), h + window // 2)
            if pred_s.lower() in text[lo:hi].lower():
                chosen = h
                break
    lo, hi = max(0, chosen - window // 2), min(len(text), chosen + window // 2)
    snippet = text[lo:hi].strip()
    if lo > 0:
        snippet = "…" + snippet
    if hi < len(text):
        snippet = snippet + "…"
    return snippet


def coverage_accuracy(preds, score_key):
    """Sort by score desc; prefix means. Ties keep input order."""
    ranked = sorted(preds, key=lambda r: (-r[score_key], r["doc_id"], r["field"]))
    n = len(ranked)
    out = []
    correct_so_far = 0
    for i, r in enumerate(ranked, 1):
        correct_so_far += int(r["correct"])
        out.append({
            "i": i,
            "coverage": i / n,
            "accuracy": correct_so_far / i,
            "threshold": r[score_key],
        })
    return out


def reliability(preds, score_key, n_bins=5):
    edges = [(i / n_bins, (i + 1) / n_bins) for i in range(n_bins)]
    n = len(preds)
    rows = []
    ece = 0.0
    for i, (lo, hi) in enumerate(edges):
        if i == n_bins - 1:
            bucket = [p for p in preds if lo <= p[score_key] <= hi]
        else:
            bucket = [p for p in preds if lo <= p[score_key] < hi]
        if not bucket:
            rows.append({"bin": f"{lo:.1f}–{hi:.1f}", "n": 0,
                         "mean_conf": None, "accuracy": None, "gap": None})
            continue
        mean_c = sum(p[score_key] for p in bucket) / len(bucket)
        acc = sum(p["correct"] for p in bucket) / len(bucket)
        gap = acc - mean_c
        ece += (len(bucket) / n) * abs(gap)
        rows.append({"bin": f"{lo:.1f}–{hi:.1f}", "n": len(bucket),
                     "mean_conf": mean_c, "accuracy": acc, "gap": gap})
    return rows, ece


def operating_points(preds, score_key, targets=ACC_TARGETS):
    """Max coverage at a real score cutoff. Ties are not split."""
    n = len(preds)
    thresholds = sorted({p[score_key] for p in preds}, reverse=True)
    by_thr = []
    for t in thresholds:
        covered = [p for p in preds if p[score_key] >= t]
        acc = sum(p["correct"] for p in covered) / len(covered)
        by_thr.append((t, covered, acc))

    points = []
    for target in targets:
        best = None
        for t, covered, acc in by_thr:
            if acc + 1e-12 >= target:
                cov = len(covered) / n
                if best is None or cov > best["coverage"]:
                    best = {
                        "target": target,
                        "threshold": t,
                        "coverage": cov,
                        "accuracy": acc,
                        "n_covered": len(covered),
                        "n_review": n - len(covered),
                        "achievable": True,
                    }
        if best is None:
            points.append({
                "target": target, "threshold": None, "coverage": 0.0,
                "accuracy": None, "n_covered": 0, "n_review": n,
                "achievable": False,
            })
        else:
            points.append(best)
    return points


def recommend(ops):
    """Prefer a 90% bar with real coverage over 99% at a handful of rows."""
    for target in (0.90, 0.95, 0.99):
        for op in ops:
            if op["target"] == target and op.get("achievable") and op["coverage"] >= 0.15:
                return op
    for op in reversed(ops):
        if op.get("achievable") and op["n_covered"] > 0:
            return op
    return ops[0]


def print_field(name, preds):
    n = len(preds)
    acc = sum(p["correct"] for p in preds) / n if n else 0
    print(f"\n{'=' * 66}")
    print(f"{name}   n={n}   overall accuracy {acc:.1%}")
    print("=" * 66)

    for signal, key in (("self-reported", "self_confidence"),
                        ("self-consistency", "consistency")):
        print(f"\n  SIGNAL: {signal}")
        table, ece = reliability(preds, key)
        print(f"  ECE = {ece:.3f}   (lower is better)")
        print(f"  {'bin':<12s} {'n':>4s}  {'mean conf':>10s}  {'accuracy':>10s}  {'gap':>8s}")
        for row in table:
            if row["n"] == 0:
                print(f"  {row['bin']:<12s} {0:4d}  {'—':>10s}  {'—':>10s}  {'—':>8s}")
                continue
            flag = ""
            if row["gap"] is not None and row["gap"] < -0.05:
                flag = "  ← overconfident"
            elif row["gap"] is not None and row["gap"] > 0.05:
                flag = "  ← underconfident"
            print(f"  {row['bin']:<12s} {row['n']:4d}  {row['mean_conf']:10.2f}  "
                  f"{row['accuracy']:10.1%}  {row['gap']:+8.2f}{flag}")

        print(f"\n  Operating points ({signal})")
        print(f"  {'target':>8s}  {'thr':>6s}  {'cover':>8s}  {'acc':>8s}  "
              f"{'auto':>6s}  {'review':>6s}")
        ops = operating_points(preds, key)
        for op in ops:
            if not op["achievable"]:
                print(f"  {op['target']:8.0%}  {'n/a':>6s}  {'0%':>8s}  {'n/a':>8s}  "
                      f"{0:6d}  {op['n_review']:6d}   not achievable")
                continue
            print(f"  {op['target']:8.0%}  {op['threshold']:6.2f}  "
                  f"{op['coverage']:8.1%}  {op['accuracy']:8.1%}  "
                  f"{op['n_covered']:6d}  {op['n_review']:6d}")
        rec = recommend(ops)
        if rec.get("achievable"):
            print(f"\n  Recommendation ({signal}): threshold {rec['threshold']:.2f} → "
                  f"auto-publish {rec['n_covered']}/{n} ({rec['coverage']:.0%}) "
                  f"at {rec['accuracy']:.1%}, route {rec['n_review']} to review "
                  f"(bar was {rec['target']:.0%}).")
        print()
    return acc


def write_html(payload, path):
    blob = json.dumps(payload, default=str).replace("<", "\\u003c")
    html = HTML_TEMPLATE.replace("/*__DATA__*/", blob)
    Path(path).write_text(html, encoding="utf-8")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Selective prediction — contract extraction</title>
<style>
  :root { --accent: #1d4ed8; --ink: #111; --muted: #667; --line: #e6e6e6; --bg: #fafafa; }
  * { box-sizing: border-box; }
  body { font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         color: var(--ink); max-width: 960px; margin: 32px auto; padding: 0 24px; }
  h1 { font-size: 20px; font-weight: 600; margin: 0 0 4px; }
  .sub { color: var(--muted); margin-bottom: 24px; }
  .control { background: var(--bg); padding: 18px 20px 14px; border: 1px solid var(--line); }
  .live { font-size: 18px; margin-bottom: 12px; }
  .live b { color: var(--accent); }
  input[type=range] { width: 100%; accent-color: var(--accent); }
  .row { display: flex; gap: 16px; align-items: baseline; color: var(--muted); font-size: 13px; }
  svg { width: 100%; height: 220px; margin: 20px 0 8px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; font-weight: 600; border-bottom: 1px solid var(--ink); padding: 6px 8px; }
  td { padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
  .conf-hi { color: #166534; }
  .conf-mid { color: #854d0e; }
  .conf-lo { color: #991b1b; }
  .queue h2 { font-size: 14px; margin: 28px 0 8px; }
  .passage { white-space: pre-wrap; font-size: 12px; color: #333; background: var(--bg);
             padding: 8px 10px; margin: 6px 0; max-height: 140px; overflow: auto;
             border-left: 3px solid var(--accent); }
  button { font: inherit; padding: 2px 10px; margin-right: 6px; cursor: pointer; }
  button.ok { color: #166534; }
  select { font: inherit; }
  .note { color: var(--muted); font-size: 12px; margin-top: 28px; }
</style>
</head>
<body>
<h1>Selective prediction</h1>
<p class="sub">Auto-publish the high-confidence extractions. Route the rest to a reviewer who can see the source.</p>

<div class="control">
  <div class="live" id="live"></div>
  <input id="thr" type="range" min="0" max="100" value="70">
  <div class="row">
    <span>Threshold <span id="thrLabel">0.70</span></span>
    <label>Field
      <select id="field">
        <option value="all">all fields</option>
        <option value="governing_law">governing_law</option>
        <option value="notice_period_days">notice_period_days</option>
        <option value="change_of_control">change_of_control</option>
      </select>
    </label>
  </div>
</div>

<svg id="chart" viewBox="0 0 640 220" aria-label="coverage accuracy curve"></svg>

<div class="queue">
  <h2 id="autoHead">Auto-published</h2>
  <table><thead><tr><th>Document</th><th>Field</th><th>Value</th><th>Conf</th></tr></thead>
  <tbody id="autoBody"></tbody></table>
</div>
<div class="queue">
  <h2 id="revHead">Routed to review</h2>
  <table><thead><tr><th>Document</th><th>Field</th><th>Value</th><th>Conf</th><th>Source</th><th></th></tr></thead>
  <tbody id="revBody"></tbody></table>
</div>
<p class="note" id="footnote"></p>
<script>
const DATA = /*__DATA__*/;
const NS = "http://www.w3.org/2000/svg";

function confClass(c) {
  if (c >= 0.95) return "conf-hi";
  if (c >= 0.8) return "conf-mid";
  return "conf-lo";
}
function shortDoc(id) {
  return id.length > 52 ? id.slice(0, 50) + "…" : id;
}
function filtered() {
  const f = document.getElementById("field").value;
  return DATA.predictions.filter(p => f === "all" || p.field === f);
}
function split(preds, thr) {
  const auto = [], review = [];
  for (const p of preds) (p.self_confidence >= thr ? auto : review).push(p);
  return {auto, review};
}
function drawChart(preds, thr) {
  const svg = document.getElementById("chart");
  svg.innerHTML = "";
  const W = 640, H = 220, pad = {l: 44, r: 16, t: 12, b: 32};
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  function el(name, attrs) {
    const n = document.createElementNS(NS, name);
    for (const [k,v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  }
  svg.appendChild(el("line", {x1: pad.l, y1: pad.t, x2: pad.l, y2: pad.t+ih, stroke: "#ccc"}));
  svg.appendChild(el("line", {x1: pad.l, y1: pad.t+ih, x2: pad.l+iw, y2: pad.t+ih, stroke: "#ccc"}));
  const ranked = [...preds].sort((a,b) => b.self_confidence - a.self_confidence);
  const pts = [];
  let ok = 0;
  ranked.forEach((p, i) => {
    ok += p.correct ? 1 : 0;
    pts.push({c: (i+1)/ranked.length, a: ok/(i+1), t: p.self_confidence});
  });
  const x = c => pad.l + c * iw;
  const y = a => pad.t + ih - a * ih;
  let d = "";
  pts.forEach((p,i) => { d += (i ? "L" : "M") + x(p.c).toFixed(1) + "," + y(p.a).toFixed(1); });
  svg.appendChild(el("path", {d, fill: "none", stroke: "#1d4ed8", "stroke-width": 2}));
  const {auto} = split(preds, thr);
  const cov = preds.length ? auto.length / preds.length : 0;
  const acc = auto.length ? auto.filter(p => p.correct).length / auto.length : 0;
  svg.appendChild(el("circle", {cx: x(cov), cy: y(acc), r: 5, fill: "#1d4ed8"}));
  const xlab = el("text", {x: pad.l+iw/2, y: H-6, "text-anchor": "middle", fill: "#667", "font-size": 12});
  xlab.textContent = "coverage";
  svg.appendChild(xlab);
  const ylab = el("text", {x: 12, y: pad.t+ih/2, fill: "#667", "font-size": 12, transform: `rotate(-90 12 ${pad.t+ih/2})`});
  ylab.textContent = "accuracy";
  svg.appendChild(ylab);
}
function render() {
  const thr = document.getElementById("thr").value / 100;
  document.getElementById("thrLabel").textContent = thr.toFixed(2);
  const preds = filtered();
  const {auto, review} = split(preds, thr);
  const n = preds.length;
  const acc = auto.length ? auto.filter(p => p.correct).length / auto.length : 0;
  const cov = n ? auto.length / n : 0;
  document.getElementById("live").innerHTML =
    `Threshold <b>${thr.toFixed(2)}</b>  →  Auto-publish <b>${auto.length}</b> (${(cov*100).toFixed(0)}%) at <b>${(acc*100).toFixed(1)}%</b>  ·  Review <b>${review.length}</b> (${((1-cov)*100).toFixed(0)}%)`;
  document.getElementById("autoHead").textContent = `Auto-published (${auto.length})`;
  document.getElementById("revHead").textContent = `Routed to review (${review.length})`;
  const ab = document.getElementById("autoBody");
  ab.innerHTML = "";
  for (const p of auto) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${shortDoc(p.doc_id)}</td><td>${p.field}</td><td>${p.value || "null"}</td><td class="${confClass(p.self_confidence)}">${p.self_confidence.toFixed(2)}</td>`;
    ab.appendChild(tr);
  }
  const rb = document.getElementById("revBody");
  rb.innerHTML = "";
  for (const p of review) {
    const tr = document.createElement("tr");
    const td = (p.passage || "").replace(/</g, "&lt;");
    tr.innerHTML = `<td>${shortDoc(p.doc_id)}</td><td>${p.field}</td><td>${p.value || "null"}</td><td class="${confClass(p.self_confidence)}">${p.self_confidence.toFixed(2)}</td><td><div class="passage">${td}</div></td><td><button class="ok" type="button">Accept</button><button type="button">Correct</button></td>`;
    rb.appendChild(tr);
  }
  drawChart(preds, thr);
}
document.getElementById("thr").addEventListener("input", render);
document.getElementById("field").addEventListener("change", render);
document.getElementById("footnote").textContent = (DATA.notes || []).join("  ·  ");
render();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("samples")
    ap.add_argument("--field", choices=FIELDS)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--gold", default="gold.csv")
    ap.add_argument("--out-json", default="calibration.json")
    ap.add_argument("--out-html", default="review_ui.html")
    ap.add_argument("--null-as-false", action="store_true", default=True)
    args = ap.parse_args()
    console_fields = FIELDS if (args.all or not args.field) else [args.field]

    if not Path(args.samples).exists():
        sys.exit(f"{args.samples} not found.")
    with open(args.gold, encoding="utf-8") as f:
        gold = {r["doc_id"]: r for r in csv.DictReader(f)}
    with open(args.samples, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("samples.csv is empty.")

    by_doc = defaultdict(list)
    for r in rows:
        by_doc[r["doc_id"]].append(r)
    for doc_id in by_doc:
        by_doc[doc_id].sort(key=lambda r: int(r["sample_idx"]))

    k_sizes = {len(v) for v in by_doc.values()}
    k = max(k_sizes)

    predictions = []
    skipped = Counter()
    for doc_id, samples in by_doc.items():
        g = gold.get(doc_id)
        if not g:
            skipped["no_gold"] += 1
            continue
        confs = [parse_conf(s.get("self_confidence")) for s in samples]
        confs = [c for c in confs if c is not None]
        self_conf = sum(confs) / len(confs) if confs else 0.0
        path = g.get("path", "")
        for field in FIELDS:
            values = [s.get(field, "") for s in samples]
            answer, consistency = modal(values)
            ok, pred_n, gold_n = score(
                field, answer, g.get(GOLD_MAP[field], ""), args.null_as_false
            )
            if ok is None:
                skipped[field] += 1
                continue
            predictions.append({
                "doc_id": doc_id,
                "field": field,
                "value": answer,
                "gold": gold_n,
                "correct": ok,
                "self_confidence": round(self_conf, 4),
                "consistency": round(consistency, 4),
                "k": len(samples),
                "model": samples[0].get("model", ""),
                "passage": passage_for(field, path, answer),
            })

    notes = [
        f"k={k} sample(s) per document. "
        + ("Consistency is 1.0 by construction — Gemini 3 has no sampling API."
           if k == 1 else "Consistency = share of samples matching the modal answer."),
        "Self-reported confidence is per-document, not per-field.",
        "n=40 documents. Bin counts are small; do not over-read empty bins.",
        "Null on yes/no fields scored as False, matching the published 82.5% ruling.",
        "Gold: CUAD v1 (CC BY 4.0, The Atticus Project). Public contracts only.",
    ]

    print("\nCALIBRATION")
    print(f"  samples: {args.samples}  docs: {len(by_doc)}  k={k}")
    print(f"  scored predictions: {len(predictions)}"
          + (f"  skipped: {dict(skipped)}" if skipped else ""))
    for line in notes:
        print(f"  · {line}")

    per_field = {}
    for field in console_fields:
        fp = [p for p in predictions if p["field"] == field]
        if not fp:
            print(f"\n{field}: no scored predictions")
            continue
        acc = print_field(field, fp)
        per_field[field] = {
            "n": len(fp),
            "accuracy": acc,
            "ece_self": reliability(fp, "self_confidence")[1],
            "ece_consistency": reliability(fp, "consistency")[1],
            "reliability_self": reliability(fp, "self_confidence")[0],
            "reliability_consistency": reliability(fp, "consistency")[0],
            "curve_self": coverage_accuracy(fp, "self_confidence"),
            "curve_consistency": coverage_accuracy(fp, "consistency"),
            "operating_self": operating_points(fp, "self_confidence"),
            "operating_consistency": operating_points(fp, "consistency"),
        }
        rec = recommend(per_field[field]["operating_self"])
        per_field[field]["recommended_self"] = rec

    payload = {
        "model": rows[0].get("model", ""),
        "k": k,
        "n_docs": len(by_doc),
        "notes": notes,
        "fields": per_field,
        "predictions": predictions,
    }
    Path(args.out_json).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    write_html(payload, args.out_html)
    print(f"\nWrote {args.out_json}")
    print(f"Wrote {args.out_html}  (open in a browser — JSON is inlined)")
    print("Drag the threshold slider. That is the exhibit.")


if __name__ == "__main__":
    main()
