# Eval Spec: [Task Name]

**Author:** Carlos
**Date:**
**Status:** Draft / Reviewed / Locked
**Data:** Public only — [CUAD v1 (CC BY 4.0, The Atticus Project) / SEC EDGAR filings]

> Write this BEFORE running anything. Lock Sections 1–5 before you look at any model
> output. If you change a rule after seeing results, log it in Section 9 with the date
> and reason — an undocumented rule change after seeing results is how an eval quietly
> becomes a demo.

---

## 1. What decision does this eval inform?

One sentence. Not "measure extraction quality" — that's an activity, not a decision.

> *Example:* Whether an analyst can accept extracted covenant terms without opening the
> source document, or whether every extraction needs a human check before it's usable.

**Your answer:**

Follow-up: what changes if accuracy comes in at 95% vs 70%? If the answer is "nothing,"
you're measuring the wrong thing.

---

## 2. Task definition

What goes in, what comes out.

- **Input:** [one contract, plain text, full document / excerpt]
- **Output:** JSON with keys: `[field_a]`, `[field_b]`, `[field_c]`
- **Abstention:** the model returns `null` when the field is genuinely absent

Decide now: **is abstention a correct answer or a failure?**

This is the highest-leverage sentence in the whole spec. In credit analysis a
confidently wrong number costs far more than a "not found," so they should probably be
scored differently — but only if you say so up front.

**Your answer:**

---

## 3. Field definitions and edge case rulings

One block per field. The edge cases are the actual product work; the definition is easy.

### Field: `[governing_law]`

**Definition:** [The US state or foreign jurisdiction whose law governs the agreement.]

**Output format:** [Bare state or country name. "Delaware", not "the laws of the State
of Delaware".]

**Edge case rulings** — decide each one *now*:

| Situation | Ruling | Why |
|---|---|---|
| Clause names a state AND excludes conflict-of-law principles | Return state only | Exclusion is boilerplate, not a different jurisdiction |
| Different law governs different sections | | |
| Governing law named only in a defined term elsewhere | | |
| Non-US jurisdiction (England and Wales, Ontario) | | |
| No governing law clause at all | | |

### Field: `[notice_period_to_terminate]`

**Definition:**

**Output format:** [Integer days. Convert months to days at 30/month. "3 months" → 90.]

| Situation | Ruling | Why |
|---|---|---|
| Different notice for cause vs convenience | | |
| Range given ("30 to 60 days") | | |
| Notice period defined by cross-reference | | |
| Expressed in business days | | |

### Field: `[uncapped_liability]`

**Definition:**

**Output format:** [`true` / `false` / `null`]

| Situation | Ruling | Why |
|---|---|---|
| Cap exists but carve-outs are uncapped | | |
| Cap applies to one party only | | |
| Silent on liability entirely | | |

---

## 4. Dataset

- **Source:**
- **N:**
- **Sampling method:** [shortest-30 for debug / random-60 for the real run]
- **Known sampling bias:**

> *Example bias statement:* The debug set is the 30 shortest contracts in CUAD, which
> skews toward simple agreements and almost certainly overstates accuracy. The reported
> headline number uses the random-60 set.

**Who labeled the gold set, and what are they qualified to judge?**

> *Example:* Gold labels for Phase 1 come from The Atticus Project's attorney
> annotations. Phase 2 labels are mine — six years in leveraged finance, but a single
> annotator with no second reader, so label noise is unmeasured except via the
> self-agreement check in Section 7.

---

## 5. Grader design

One row per field. The point of this table is to be explicit that grading is a
*choice*, not a fact.

| Field | Grader type | Normalization applied | Known weakness |
|---|---|---|---|
| governing_law | exact match after normalize | lowercase, strip "the state of", strip conflicts clause | Won't catch a semantically right answer phrased unusually |
| notice_period | numeric exact | months→days at 30 | 30/month is a convention, not a fact |
| uncapped_liability | LLM judge + rubric | — | Judge reliability unknown until Section 7 |

**Judge rubric** (for the judgment field) — write the actual prompt text here:

```
[Your rubric. Be specific about what counts as true, what counts as false,
and what the judge should do when the extraction is ambiguous.]
```

---

## 6. Hypotheses

Two or three. Each must be **falsifiable before you run** — if you can't state what
result would prove it wrong, rewrite it.

**H1:**
> *Example:* Most failures will be retrieval failures (found the wrong clause) rather
> than comprehension failures (found the right clause, read it wrong). Falsified if
> fewer than half of errors trace to a wrong source span.

**H2:**
> *Example:* Adding two edge-case rulings to the prompt will recover more accuracy per
> dollar than upgrading from Haiku to Sonnet. Falsified if the model upgrade wins on
> accuracy-per-dollar.

**H3:**

---

## 7. Reliability checks

- [ ] **Judge agreement.** 30 items double-labeled. Report raw agreement + Cohen's kappa.
      Target κ ≥ 0.6. Report the pre-fix number too.
- [ ] **Self-agreement (ceiling).** Re-label 20 items ≥5 days later, blind to your
      originals. Your disagreement rate is the effective ceiling — the model cannot
      meaningfully beat it, and some share of "model errors" are label noise.
- [ ] **Parse failure rate.** Malformed JSON is a real failure mode. Count it separately
      from wrong answers.

Measured self-agreement: ______  → effective ceiling: ______

---

## 8. Results

### Headline

| Field | v1 | v2 | Δ | Ceiling |
|---|---|---|---|---|
| governing_law | | | | |
| notice_period | | | | |
| uncapped_liability | | | | |

### Failure taxonomy

| Failure mode | Count | % of errors | Example doc | Addressable by prompt? |
|---|---|---|---|---|
| | | | | |
| | | | | |
| | | | | |
| | | | | |

### Cost and latency

| Config | Accuracy | $/doc | p95 latency | Notes |
|---|---|---|---|---|
| v1 | | | | |
| v2 | | | | |
| bigger model | | | | |

### Slices

| Slice | N | Accuracy |
|---|---|---|
| Doc under 10k tokens | | |
| Doc over 40k tokens | | |
| Term defined inline | | |
| Term by cross-reference | | |

---

## 9. Change log

Every rule you changed after seeing results. Be honest; this section is a credibility
asset, not a confession.

| Date | Section | Change | Reason | Did results move? |
|---|---|---|---|---|
| | | | | |

---

## 10. Ship decision

The section hiring managers actually read.

**What I'd ship:**

**What I'd gate behind human review:**

**Confidence threshold and abstention behavior:**

> *Example:* Below the threshold the system returns "not found" with a link to the
> candidate section rather than a guess. Abstention is cheap; a wrong covenant threshold
> that reaches a credit memo is not.

**What I'd tell a customer this cannot do:**

**Regression suite:** which N examples get re-run on every prompt or model change, and
what accuracy drop blocks a release?

**What I'd measure in production that this offline eval can't tell me:**

> *Example:* Offline accuracy says nothing about whether analysts actually trust the
> output. Instrument override rate — how often a human edits an extracted value — and
> treat a rising override rate as the real regression signal.

---

## 11. Limitations

Write these yourself before an interviewer writes them for you.

-
-
-
