# Four Ways My Eval Lied to Me

### Building an evaluation harness for contract clause extraction — and discovering that most "model failure" was measurement failure

**Carlos Abreu** · Product Manager, CreditSights (Fitch Group)
Data: CUAD v1 (CC BY 4.0, The Atticus Project) — public contracts only.
No employer data, customer documents, internal prompts, or proprietary metrics appear anywhere in this project.

---

## The short version

I built an eval harness to measure how well an LLM extracts three fields from commercial contracts: governing law, termination notice period, and whether a change-of-control provision exists.

The first run scored 80% / 77.5% / 60%. Thirty-three of 120 outputs were marked wrong.

I read all 33 by hand. **Seven were the model misreading a contract.** The other 26 were my measurement apparatus: a grading rule I never specified, a retrieval bug I had written, errors in the benchmark's own answer key, and a unit convention.

| What the failure actually was | Count |
|---|---:|
| Grading rule never specified (null vs. False) | 13 |
| Retrieval bug — excerpt dropped the clause | 6 |
| Benchmark label was wrong | 5 |
| Grader unit convention (360 vs. 365 days) | 2 |
| **Model misread the contract** | **7** |
| | **33** |

Had I shipped that first number, I would have reported that change-of-control detection was unreliable at 60%. It wasn't. It was 82.5%, and the gap was entirely mine.

That is the finding. Not an accuracy score — a demonstration that an eval is an instrument, that instruments have systematic defects, and that the defects are invisible from the summary statistics they produce.

---

## Why this task

I spend my working life on leveraged-finance credit agreements. Covenant analysis is exactly this problem at four times the document length and considerably higher stakes: does this agreement contain a change-of-control event of default, what triggers the mandatory prepayment, how is the notice period defined.

I deliberately did **not** use employer data. CUAD is public, attorney-annotated, and openly licensed, which means anyone can reproduce and challenge every number here. Working on the generic version of the problem also made the transferable lesson clearer than working on the version where I already know the answers.

I chose the three fields for contrasting grading properties, not for interest:

| Field | Type | Why it's here |
|---|---|---|
| Governing law | String, 15 distinct values | Tests normalization — is "the laws of the State of Delaware" the same answer as "Delaware"? |
| Notice period | Numeric, 15 distinct values | Tests unit conversion — months, days, years, cross-references |
| Change of control | Boolean, 25 No / 15 Yes | Tests judgment, and forces the abstention question |

---

## Method

**Corpus.** CUAD v1, 510 contracts. After excluding documents under 15,000 characters — CUAD contains cover pages and stub exhibits that aren't contracts — 396 remained. The notice-period annotation exists in only 91 of those, so I drew 40 at random from that subset.

**That sample is conditioned and therefore not representative.** Contracts with auto-renewal notice periods are a specific subgroup. Every number here describes that subgroup, not CUAD as a whole.

Median document: 46,679 characters. Largest: 225,639.

**Model.** `gemini-3.1-flash-lite`, temperature 0, September 2026. Roughly 744,000 input tokens across all runs, $0.00 on the free tier. Median latency 1.9s.

*(Model version is stamped deliberately. Mid-project, a model ID I'd hardcoded was retired by the provider between one run and the next. An eval result without a model version attached is undated data.)*

**Retrieval.** Sending a 225,000-character contract to answer "what state's law governs?" is wasteful and, as it turned out, actively harmful. I built a keyword-anchored excerpter that pulls ~1,800-character windows around clause-indicating phrases. Median coverage: 24% of the document.

**Grading.** Deterministic — normalized exact match per field, no LLM judge. Per-class recall reported alongside accuracy on the boolean field.

**Three arms, one variable at a time:**

| Arm | Retrieval | Prompt |
|---|---|---|
| A | Fill budget in document order | Bare field definitions |
| B | Round-robin budget across field types | Bare field definitions |
| C | Round-robin budget across field types | Explicit edge-case rules |

---

## Results

| Field | A | B | C |
|---|---:|---:|---:|
| Governing law | 80.0% | 95.0% | 95.0% |
| Notice period | 80.0% | 85.0% | 80.0% |
| Change of control | 60.0% | 62.5% | 82.5% |

Decomposed, with McNemar's exact test on the paired outcomes:

| Field | Retrieval fix (A→B) | Prompt fix (B→C) |
|---|---|---|
| Governing law | **+15.0 pts** (6 fixed, 0 broken, p=.031) | 0.0 (0, 0) |
| Notice period | +5.0 (2, 0, p=.50, n.s.) | **−5.0** (0, 2, p=.50, n.s.) |
| Change of control | +2.5 (3, 2, p=1.00, n.s.) | **+20.0 pts** (11 fixed, 3 broken, p=.057) |

**Three things fall out of this table.**

**Interventions are field-specific.** The retrieval fix did essentially nothing for change of control. The prompt fix did nothing at all for governing law. There is no single lever.

**The prompt fix regressed a field it wasn't aimed at.** Notice period lost two documents to a rule written for change of control. A blended accuracy across all three fields would have shown both interventions as modest uniform wins and hidden the regression completely.

**Only two of six effects survive significance testing.** At n=40, one document is 2.5 points, and anything under roughly 10 points is indistinguishable from noise. The +5 and −5 on notice period are directional, not results. Saying so is part of the finding.

### The retrieval bug, in the size distribution

My first excerpter merged keyword windows in document order and stopped at a 30,000-character budget. Governing law sits in the closing "Miscellaneous" section of almost every contract. On long documents, I was systematically truncating away the exact clause I was asking for.

The six documents recovered by the fix have a **median size of 133,611 characters**, against **46,628** for the sample overall. Five of six exceed 96,000.

| Document | Size | Coverage A → B |
|---|---:|---|
| Upjohn (Manufacturing) | 225,094 | 12% → 17% |
| OFG Bancorp (Outsourcing) | 159,336 | 17% → 25% |
| Pacira (Licensing) | 145,168 | 19% → 27% |
| Moelis (Strategic Alliance) | 122,054 | 22% → 33% |
| iPayment (Sponsorship) | 96,662 | 31% → 40% |
| Pacificap (8-K exhibit) | 18,138 | 13% → 42% |

Every failure was the model returning blank — never a wrong jurisdiction. Retrieval failure, not comprehension failure, and the evidence is a size distribution rather than an argument.

### The abstention trade

Arm A returned `null` on change of control for 13 of 40 documents. My grader scored `null ≠ False` as wrong, which is where 13 of the 16 "failures" came from.

The v2 prompt forced a verdict. Nulls went 13 → 0, accuracy went 60% → 82.5%, and three previously correct answers became confident wrong ones.

The remaining error profile is worth stating precisely:

**7 false positives. 0 false negatives.**

The system never misses a real change-of-control provision. It invents seven that aren't there. It also no longer has any way to say "I don't know" — I deleted the abstention signal to buy 20 accuracy points, and I'd want that decision made explicitly rather than as a side effect of a prompt edit.

---

## The four instrument defects

Each of these produced a plausible number. None would have been caught by looking at summary statistics.

**1. Sampling that quietly destroyed the dataset.** My first sample took the 30 shortest contracts, for cost reasons. Shortest also means simplest. The resulting set had 5 notice periods out of 30 and a 90/10 class split on change of control. Random sampling from the eligible pool gave 40/40 and 25/15. Same corpus, same code — the sampling method alone changed the dataset's usability.

**2. A metric a constant would have won.** At 90/10, a system that ignores the contract and always answers "No" scores 90%. I nearly reported accuracy on that field. Per-class recall exposed it: one configuration scored 60% overall while catching 0% of the positive class.

**3. A grading rule I never wrote down.** Does abstention on an absent clause count as correctly reporting absence? I never answered that, so the grader answered it for me by treating `null` as a wrong answer. Thirteen failures — the single largest category in the taxonomy — came from an unwritten rule.

**4. A grader that wasn't deterministic.** My jurisdiction normalizer iterated a Python `set` to find the first matching state name. Set iteration order varies between processes. A label naming two jurisdictions normalized differently on different runs, so arms A and C were scored against **different answer keys**. The same bug meant "West Virginia" could match as "Virginia."

I found it because one document's gold value changed between two runs of the same script.

**And a fifth, which isn't mine:** five of 40 benchmark labels are wrong — three notice periods, one change of control, one malformed governing-law string. That is a ~12% label error rate in an attorney-annotated dataset used as a standard benchmark, and it caps what any model can score against it.

Most of this harness was AI-generated. The set iteration, the budget truncation, the null handling — I didn't write those lines, I reviewed their outputs and found the defects. That is the job: validating a system you didn't hand-write, catching failures the system reports as successes.

---

## What I'd do as a PM

**Ship governing-law extraction.** 95%, and the two residual failures are both grader artifacts rather than model errors. Retrieval is solved for this field.

**Ship change-of-control detection as a screening aid, not an answer.** The error profile — 7 false positives, 0 false negatives — is the right asymmetry for covenant review. An analyst dismisses a false flag in seconds. A missed change-of-control provision reaches a credit memo. Surface it as "possible CoC language, review §8.2" with the source passage attached, never as a populated field.

**Don't ship notice period.** 80%, sitting near a ceiling set by label noise, with no intervention that moved it. The failure mode is silent — a wrong integer in a field looks exactly like a right one.

**Restore abstention, and route it.** v2's zero-null rate is a liability, not an achievement. The production design needs three states: confident answer, abstention with the candidate passage, and flagged-for-review. I traded away the signal that tells you which documents need a human.

**Regression suite.** These 40 documents, re-run on every prompt or model change, with per-field and per-class thresholds. A 5-point drop on change-of-control positive-class recall blocks release regardless of what aggregate accuracy does.

**The production metric isn't accuracy — it's analyst override rate.** How often does a human edit an extracted value? Offline accuracy against a benchmark measures agreement with annotators. Override rate measures whether the people doing the work trust the output. They come apart, and the second one is what determines whether a feature gets used.

**And before any of it: inter-annotator agreement.** I attempted to measure my own agreement with CUAD's labels and abandoned the attempt (see Limitations). That check belongs *before* the eval, not after. If two qualified readers don't agree on what the right answer is, no accuracy number computed against those labels means what it appears to mean — and in covenant work, terms like "change of control" carry meaningfully different scope for a credit analyst than for a commercial-contracts attorney.

---

## Limitations

- **n=40.** One document = 2.5 points. Resolution is roughly ±10 points; only two of six measured effects clear it. The full eligible population is 91 documents, which would bring this to about ±7.
- **Conditioned sample.** Drawn from contracts that have an auto-renewal notice period. Not representative of CUAD or of commercial contracts generally.
- **No inter-annotator agreement measured.** I ran a labeling pass to establish a ceiling and did not complete it attentively enough to trust, so I discarded it rather than report it. The ceiling is therefore unmeasured, and the five label errors found by inspection suggest it is not negligible. This is the largest open gap in the project.
- **One model, one provider, one date.** No cross-model comparison. Results are not portable across model versions.
- **Single annotator on the failure review.** My classification of the 33 failures has no second reader.
- **Deterministic grading only.** No LLM-as-judge, so no judge-calibration work.

---

## Reproduce it

Five Python files, no dependencies beyond the standard library, ~1,100 lines.

```bash
python3 00_prepare.py --require notice_period --n 40
python3 01_extract.py --excerpt --prompt-version v2 --model lite
python3 02_grade.py results_v2_excerpt.csv
```

The harness includes the guardrails this project taught me it needed: a class-balance check that refuses to proceed on a degenerate sample, per-class recall on boolean fields, deterministic normalization, an ambiguity flag for unitless labels, and a check that refuses to report agreement statistics against a constant rater.

Every one of those exists because the absence of it produced a wrong number I almost believed.
