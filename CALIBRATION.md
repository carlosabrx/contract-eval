# Neither Confidence Signal Worked

### Testing selective prediction on LLM contract extraction — and finding that the standard techniques produce nothing to threshold on

**Carlos Abreu** · [carlosabrx](https://linkedin.com/in/carlosabrx)

A side project, built on public data, on my own time. Data: CUAD v1 (CC BY 4.0, The Atticus Project).
Follow-on to [Four Ways My Eval Lied to Me](FINDINGS.md). Same 40 contracts, same harness.

---

## The short version

An extraction system that is 87.5% accurate is not shippable as-is. The usual next move is selective prediction: attach a confidence score, auto-publish above a threshold, route the rest to human review. Tune the threshold to trade coverage against accuracy.

I tested the two confidence signals available without training anything.

**Both failed, in different ways.**

| Signal | Separates right from wrong? | Enough resolution to threshold? |
|---|---|---|
| Self-reported confidence | Yes — 19.5 pts | **No** — 3 distinct values, 85% identical |
| Self-consistency (k=5) | Unmeasurable | **No** — 119 of 120 unanimous |

You cannot tune a threshold on a signal that emits three values, and you cannot measure disagreement in a model that does not disagree with itself. Selective prediction on this task needs a mechanism outside the model's own output.

That's a more useful thing to know than a curve that behaved, and it cost 920,625 input tokens to find out.

---

## What I set out to test

**Hypothesis, written before the run:** self-reported confidence will rank answers reasonably but be badly calibrated — systematically overconfident, clustering at 90–95 regardless of difficulty. Self-consistency will rank better and calibrate better. *Falsified if self-reported ECE is lower.*

The first half was right. The second half never got a chance, for a reason worth its own section.

---

## Signal A — self-reported confidence

One extra field on the same JSON object: `"confidence": <integer 0-100>`.

**It ranks.**

| | n | Accuracy |
|---|---:|---:|
| Confidence = 100 | 99 | 90.9% |
| Confidence < 100 | 21 | 71.4% |
| **Separation** | | **+19.5 pts** |

That's real signal. Low-confidence answers are meaningfully more likely to be wrong.

**It's overconfident.**

| | |
|---|---:|
| Mean confidence | 98.8% |
| Actual accuracy | 87.5% |
| **Gap** | **+11.3 pts** |

**And it has almost no resolution.** Across 40 documents the model emitted three distinct values: 90, 95, and 100. Thirty-four documents reported exactly 100.

| Threshold | Coverage | Accuracy |
|---|---:|---:|
| conf = 100 | 85% | 91.2% |
| conf ≥ 95 | 92.5% | 89.8% |
| conf ≥ 90 | 100% | 87.5% |

Three operating points. Not a curve — a staircase with three steps. There is no threshold that covers 50% or 70% of documents, because there is nothing to sort on in that range.

**The product consequence:** this signal supports exactly one decision. Drop the bottom 15% and gain 3.7 points. If the accuracy bar is 95%, no threshold reaches it, and no amount of tuning will change that.

A roadmap that says "ship a confidence score, then tune the threshold" assumes the score has enough distinct values to tune across. This one doesn't.

---

## The ECE trap

Run standard calibration metrics on governing law and you get ECE = 0.038, which reads as well calibrated.

It isn't. It's a coincidence.

Confidence is pinned near 0.99 and accuracy on that field happens to be 95%, so the gap is small. Had accuracy been 70%, the identical signal would score ECE 0.29. **A constant cannot be miscalibrated in ranking, because it doesn't rank.**

Calibration and resolution are different properties, and ECE only measures the first. A signal can be perfectly calibrated and completely useless. Any confidence evaluation needs both numbers — the calibration error *and* the distribution of distinct values — because the first one alone will tell you things are fine when there is nothing there.

---

## The confidence moves more than the answer does

Five of 40 documents returned a different confidence value across identical calls while the extracted answer never changed.

| Document | Answer across 5 calls | Confidence across 5 calls |
|---|---|---|
| Upjohn | Delaware ×5 | 95, 90, 90, 90, … |
| TubeMedia | New York ×5 | 95, 95, 100, 100, … |
| Vapotherm | New York ×5 | 90, 90, 95, 95, … |
| Pacira | New York ×5 | 95, 95, 95, 90, … |

The prediction is stable. The model's stated certainty about that prediction wobbles by 5 points.

That is close to a definition of an uninformative signal, and it is invisible in a single run.

---

## Signal B — self-consistency, and why it couldn't be measured

The standard technique: sample the same prompt k times at temperature > 0, and use the share of samples agreeing with the modal answer as confidence.

**The provider removed the controls this depends on.** Gemini 3 ignores `temperature`, `seed`, and `candidateCount`. Gemini 2.5's `flash-lite` returns 404 for new API keys — listed by the models endpoint, then refused on call.

Ignoring a temperature request and being deterministic are different things, so I probed rather than assumed: 10 documents × 3 identical calls. One document varied. Enough to justify the full run.

**Result at k=5, 200 samples, 40 documents:**

| Field | Documents with any disagreement |
|---|---|
| Governing law | 0 of 40 |
| Notice period | 0 of 40 |
| Change of control | 1 of 40 |

**One of 120 (document, field) pairs. A 0.83% run-to-run variance rate.**

Every document scored consistency = 1.00 except one, which means the signal is a constant and there is nothing to threshold.

The single document that wavered — `[False, False, False, True, False]` on change of control — had a **correct** modal answer. The one inconsistent case was one the model got right, which is the opposite of what the technique assumes. n=1, so it's an anecdote, but it isn't encouraging.

**Cost:** 920,625 input tokens, five times the single-pass cost, for one document of signal.

---

## What I'd do as a PM

**Don't ship a confidence threshold on this task.** Not because the model is bad — 87.5% overall, 95% on governing law — but because the confidence signal has three values and no mechanism exists to make it finer.

**Do ship the one decision the signal supports.** Route the ~15% below maximum confidence to review. That buys 3.7 accuracy points on the published set for 15% review load. It's a switch, not a dial, and it should be described that way to stakeholders.

**Measure resolution before promising tunability.** The distribution of distinct confidence values is a one-line check that should run before any threshold appears in a roadmap. Mine would have caught this in the first hour.

**For finer control, the mechanism has to come from outside the model's own output:**
- A separate verifier model scoring the extraction against the source passage
- Retrieval-grounded checks — does the cited span actually contain the claimed value?
- An ensemble across *different* models rather than repeated draws from one, since repeated draws from a deterministic model return the same answer by construction
- Token-level logprobs, if the provider exposes them

**Report both calibration and resolution, always.** ECE alone would have passed this signal.

**And treat provider control surfaces as a product dependency.** This experiment was designed around sampling parameters that existed when it was specified and were ignored by the model when it ran. That is a normal condition of building on someone else's model, and a plan that assumes those knobs will be there needs a fallback.

---

## Limitations

- **n=40 documents, one model, one date.** `gemini-3.1-flash-lite`, September 2026. Nothing here is portable across model versions.
- **The conditioned sample** carries over from the first study — drawn from contracts that have an auto-renewal notice period, so not representative of CUAD.
- **Self-reported confidence is per-document, not per-field.** The model reports one number for a three-field JSON object, so it cannot express "sure about governing law, unsure about notice period." Self-consistency would have been per-field, which is part of why it was worth testing.
- **Self-consistency was never tested on a model that honors temperature.** `gemini-2.5-flash` accepts it, but running one signal on one model and the other on another would confound the comparison, and running both on 2.5 was more budget than the finding justified. The honest claim is narrow: on this model, self-consistency is not measurable.
- **5 bins on ~40 documents per field.** Several bins are empty. Bin counts are reported; don't over-read them.
- **Correction to the prior study:** I initially estimated run-to-run variance at ~3% from a 30-call probe. The full 600-sample run puts it at 0.83%. The earlier figure was 3.6× too high, and any conclusion resting on it should use the corrected number.

---

## The interface

`review_ui.html` — self-contained, data inlined, opens offline.

A threshold slider that updates coverage and accuracy live, the coverage–accuracy curve with a marker at the current threshold, and two queues: auto-published, and routed-to-review with the source passage attached to each item.

The source passage is the part that matters. An extraction tool that flags low confidence and makes a reviewer open a 200-page document has relocated the work rather than reduced it.

Dragging the slider is also the fastest way to see this study's finding. The numbers barely move across most of the range, because there is almost nothing to sort on.
