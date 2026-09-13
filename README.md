# contract-eval
# Contract Clause Extraction Eval

An evaluation harness for LLM clause extraction, plus a write-up of what building it taught me about eval design.

**[→ Read the findings](FINDINGS.md)**

---

## The one-line version

First run scored 80% / 77.5% / 60% across three fields. I hand-audited all 33 failures. Seven were the model misreading a contract; 26 were defects in my own measurement apparatus.

| What the failure actually was | Count |
|---|---:|
| Grading rule never specified (null vs. False) | 13 |
| Retrieval bug — excerpt dropped the clause | 6 |
| Benchmark label was wrong | 5 |
| Grader unit convention (360 vs. 365 days) | 2 |
| **Model misread the contract** | **7** |

---

## Results

Three arms, one variable changed at a time, McNemar's exact test on paired outcomes.

| Field | A | B | C | Retrieval fix (A→B) | Prompt fix (B→C) |
|---|---:|---:|---:|---|---|
| Governing law | 80.0% | 95.0% | 95.0% | **+15.0** (p=.031) | 0.0 |
| Notice period | 80.0% | 85.0% | 80.0% | +5.0 (n.s.) | **−5.0** (n.s.) |
| Change of control | 60.0% | 62.5% | 82.5% | +2.5 (n.s.) | **+20.0** (p=.057) |

Interventions are field-specific and non-additive. The prompt fix regressed a field it wasn't aimed at. A blended accuracy across the three fields would have hidden that entirely.

---

## Setup

No pip install. No SDKs. Python 3.9+ and standard library only.

```bash
# 1. Get a free Gemini API key at https://aistudio.google.com/apikey
export GEMINI_API_KEY='your-key-here'

# 2. Download CUAD v1 from https://zenodo.org/records/4595826
#    Unzip full_contract_txt/ and master_clauses.csv into this folder

# 3. Verify connectivity
python3 llm.py

# 4. Run it
python3 00_prepare.py --require notice_period --n 40
python3 01_extract.py --excerpt --prompt-version v2 --model lite
python3 02_grade.py results_v2_excerpt.csv
```

Total cost of every run in this project: **$0.00** (~744,000 tokens, free tier).

---

## Files

| File | Purpose |
|---|---|
| `00_prepare.py` | Builds the gold set from CUAD annotations. Length floor, stratified sampling, class-balance audit across all 41 CUAD categories. |
| `01_extract.py` | Runs extraction. Keyword-anchored excerpting with round-robin budget allocation. Resumable. |
| `02_grade.py` | Normalized exact-match grading. Per-class recall, failure inspection, ambiguity flagging. |
| `03_agreement.py` | Three-way agreement between you, the benchmark, and the model. Measures label quality rather than assuming it. |
| `llm.py` | Provider-agnostic client (Gemini / Anthropic) over `urllib`. Client-side rate limiting. |

Switch models with `--model lite | gemini-flash | sonnet | haiku | opus`.

---

## Guardrails built in

Each of these exists because its absence produced a wrong number I nearly believed.

- **Class-balance warning before any API call.** Flags a field where one answer holds ≥75% of labels, because a constant-output system already scores that share.
- **Per-class recall on boolean fields.** One configuration scored 60% overall while catching 0% of the positive class. Aggregate accuracy showed a working system.
- **Deterministic normalization.** The jurisdiction matcher originally iterated a Python `set`; iteration order varies between processes, so two runs were scored against different answer keys.
- **Length floor on document selection.** CUAD contains cover pages and stub exhibits that aren't contracts.
- **Ambiguity flag for unitless labels.** A gold value of `"6"` (meaning six months) scored as six days and marked a correct answer wrong.
- **Degenerate-rater detection.** `03_agreement.py` refuses to report agreement statistics when any rater gave the same answer every time, because kappa is 0 by construction and every derived figure measures the constant.

---

## Method notes

- **Corpus:** CUAD v1 (CC BY 4.0, The Atticus Project). 510 contracts → 396 over 15k chars → 91 with a notice-period annotation → 40 sampled at random.
- **Sample is conditioned** on the notice-period field being present, and is therefore not representative of CUAD.
- **Model:** `gemini-3.1-flash-lite`, temperature 0, September 2026. Results are not portable across model versions.
- **n=40** gives roughly ±10 point resolution. Only two of six measured effects clear it.
- **Five of 40 benchmark labels are wrong** — a ~12% error rate that caps achievable accuracy against them.

Full limitations in [FINDINGS.md](FINDINGS.md#limitations).

---

## Data and confidentiality

Public data only. CUAD is openly licensed; this repo contains no employer data, customer documents, internal prompts, or proprietary metrics.

Credit: [The Atticus Project](https://www.atticusprojectai.org/cuad) for CUAD v1.
