# Setup: Contract Clause Extraction Eval

Beginner-oriented. Follow in order. Nothing here assumes you've written Python before.

Estimated time to first results: **90 minutes**, most of it downloading and waiting.

---

## What you're building

A script that reads contracts, asks Claude to pull three specific fields out of each one,
writes the answers to a CSV, and then scores those answers against known-correct labels.

That's it. Four small Python files. No web app, no database, no deployment.

---

## Step 0 — Install the three things you need

### Python

Open Terminal (Mac: Cmd+Space, type "Terminal") and run:

```bash
python3 --version
```

If you see `Python 3.10` or higher, you're done. If you get "command not found",
install from https://www.python.org/downloads/ — take the default options.

### Cursor

Download from https://cursor.com. Install it and open it.

Cursor is a text editor with a chat panel attached. It's Visual Studio Code with
Claude wired in. You will use it for two things:
- Editing these files
- Asking it "what does this error mean?" when something breaks

You do **not** need to learn Cursor. Open a folder, edit files, use Cmd+L to ask questions.
That's the whole surface area for this project.

### An API key

You have two options. **Start with Gemini — it's free and sufficient.**

**Option A — Google AI Studio (free, no credit card):**

1. Go to https://aistudio.google.com/apikey
2. Sign in with any Google account
3. Click "Create API key"
4. Copy it

The free tier is permanent, not a trial. Flash-class models only, roughly 10–15
requests per minute and 1,000–1,500 per day. Your whole project is ~180 requests, so
the daily cap is irrelevant; the per-minute token cap is what throttles you. The
scripts handle that automatically.

Note: a Google AI Pro subscription does NOT by itself grant API access — plan benefits
apply inside the AI Studio web interface only. The free API tier above is separate and
open to anyone. If you're an AI Pro subscriber you can additionally activate monthly
Cloud credits at https://google.dev ("Activate Developer Benefits"), which unlocks
Pro-class models. Optional; Flash is fine for this project.

**Option B — Anthropic (paid):**

1. https://console.anthropic.com → Settings → API Keys → Create Key
2. Add $10 credit under Billing (a Claude Pro plan does NOT include API credits)

Worth doing only if you want to compare providers, which is a legitimate depth axis:
same eval, two models, report accuracy per dollar.

---

## Step 1 — Get the files and the data

### Create your project folder

```bash
mkdir -p ~/contract-eval
cd ~/contract-eval
```

Copy the four `.py` files and `eval-spec-template.md` into this folder.

### Download CUAD

CUAD v1 lives on Zenodo: **https://zenodo.org/records/4595826**

Download `CUAD_v1.zip` (about 106 MB). Unzip it. You want two things out of it:

- `CUAD_v1/full_contract_txt/` — 510 plain-text contracts
- `CUAD_v1/master_clauses.csv` — the expert labels

Move both into your project folder so you have:

```
~/contract-eval/
├── full_contract_txt/        (510 .txt files)
├── master_clauses.csv
├── 00_prepare.py
├── 01_extract.py
├── 02_grade.py
├── 03_agreement.py
└── eval-spec-template.md
```

CUAD is CC BY 4.0 licensed, so you can publish results from it. Credit The Atticus
Project in your write-up.

---

## Step 2 — Set up the environment

In Terminal, from your project folder:

```bash
python3 -m venv venv
source venv/bin/activate
pip install google-genai pandas        # Option A (free)
# pip install anthropic pandas         # Option B, or both to compare
```

`venv` is an isolated Python environment so this project's packages don't collide with
anything else on your machine. You'll see `(venv)` appear at the start of your prompt.

**Every new Terminal session, you must re-run `source venv/bin/activate` first.**
Forgetting this is the single most common beginner stumble.

Now set your API key:

```bash
export GEMINI_API_KEY='your-key-here'          # Option A
# export ANTHROPIC_API_KEY='sk-ant-...'        # Option B
```

This also resets when you close Terminal. To make it permanent:

```bash
echo "export GEMINI_API_KEY='your-key-here'" >> ~/.zshrc
```

Never put the key inside a `.py` file. If you push this repo to GitHub with a key in it,
the key gets scraped within hours.

---

## Step 3 — Write the spec BEFORE you run anything

Open `eval-spec-template.md` in Cursor. Fill it in.

This will feel like procrastination. It is not. Every hour you spend here saves three
hours of "wait, is this answer actually wrong?" on Sunday.

Specifically: write down your edge-case rulings *before* you see model output. If you
decide the rules after seeing the failures, you will unconsciously write rules that
make the model look better, and the whole eval becomes worthless.

Budget 45 minutes. Do not skip.

---

## Step 4 — Build the gold set

```bash
python3 00_prepare.py
```

This reads `master_clauses.csv`, finds the columns for your three fields, picks 30
contracts, and writes `gold.csv`.

It picks the 30 **shortest** contracts on purpose — they're cheap to run while you're
debugging. That is a biased sample and you should say so in your spec. Once the
pipeline works, re-run with `--sample random --n 60` for the real run.

It prints every column name it found. If the matching looks wrong, the column names in
your copy of CUAD differ from what the script expects — edit the `FIELDS` dict at the
top of the file. Ask Cursor for help if the error is opaque.

---

## Step 5 — Run the extraction

Start tiny:

```bash
python3 01_extract.py --limit 3
```

Three contracts. Costs a couple of cents. Confirms your key works, the API responds,
and the CSV writes correctly.

Look at `results_v1.csv`. Open it in Excel. Does it have three rows with plausible
answers in them?

If yes:

```bash
python3 01_extract.py
```

Full run. Takes 5–15 minutes. The script caches — if it crashes halfway, re-running
skips whatever it already finished, so you never pay twice for the same document.

It prints token counts and a cost estimate ($0.00 on the Gemini free tier). Token
counts are the number to trust; dollar figures are a rough guide.

To switch models or providers, pass `--model`:

```bash
python3 01_extract.py --model gemini-flash        # default, free
python3 01_extract.py --model gemini-flash-lite   # faster, weaker
python3 01_extract.py --model sonnet              # needs ANTHROPIC_API_KEY
python3 01_extract.py --model haiku
```

Record the exact model ID and date in your spec. Results are not portable across
model versions, and an eval without a model version stamped on it is undated data.

---

## Step 6 — Grade

```bash
python3 02_grade.py results_v1.csv
```

Prints per-field accuracy and writes `graded_v1.csv` with a `correct` column.

**Your first accuracy number will be misleadingly low.** Roughly a third of apparent
errors will be your grader being too strict — "Delaware" vs "the laws of the State of
Delaware" vs "Delaware (excluding conflicts of law principles)". All three are right.

Go fix the normalizer in `02_grade.py` until the exact-match grader agrees with your own
judgment. **This is real work, not setup.** Write down what you changed and why. It
goes in the write-up, and it's a better interview story than the accuracy number itself,
because it's where you learn that "accuracy" is a design decision rather than a
measurement.

---

## Step 7 — Read the failures

Open `graded_v1.csv`. Filter to `correct = False`. Read every single one.

Not skim. Read.

Write a name next to each: `wrong_section`, `hedged_instead_of_answering`,
`picked_boilerplate`, `json_malformed`, `label_actually_wrong`, and so on. Aim for
4–6 categories total. Count them.

That table is the centerpiece of your write-up. Everything else is supporting material.

---

## Step 8 — Intervene, once

Pick the single largest failure category. Write `v2` of the prompt in `01_extract.py`
targeting *only* that category. Then:

```bash
python3 01_extract.py --prompt-version v2
python3 02_grade.py results_v2.csv
```

Compare. One variable changed, so you can actually attribute the difference.

Resist changing three things at once. If you change the prompt and the model and the
temperature, you've learned nothing and you'll have to say so out loud in an interview.

---

## Step 9 — Judge agreement (the differentiating step)

`uncapped_liability` is a yes/no judgment, not a string match. So grade it with an LLM
judge, then check whether the judge is any good:

```bash
python3 03_agreement.py
```

This has you hand-label 30 outputs, runs the judge on the same 30, and reports raw
agreement plus Cohen's kappa.

**Kappa below ~0.6 means your rubric is ambiguous, not that the judge is dumb.** Rewrite
the rubric, re-measure, report both numbers.

Reporting a bad first kappa and the fix is a *stronger* result than reporting a good one.
It shows you know the judge is a measurement instrument that needs calibrating, which is
the thing most portfolio projects miss entirely.

---

## Step 10 — Phase 2: swap in credit agreements

Same code. Different documents.

1. Go to https://www.sec.gov/edgar/search/ (full-text search)
2. Search for `"Consolidated EBITDA"` filtered to exhibit types EX-10
3. Download 40–60 credit agreements as text
4. Put them in `credit_agreements_txt/`
5. Hand-label your gold set — this is where your day job makes you fast
6. Change `--docs-dir` and the field definitions, rerun everything

**Two practical warnings for Phase 2:**

**Length.** Credit agreements run 200+ pages. Sending one whole is expensive and the
model does worse anyway. Pre-slice: regex out the `"Consolidated EBITDA"` definition
plus surrounding context, feed only that. This is also what you'd do in production, and
the gap between whole-doc and sliced performance is itself a finding worth reporting.

**Data handling.** Free-tier API inputs may be used by the provider for product
improvement. Irrelevant here since CUAD and EDGAR are both public — but state it
explicitly in your spec anyway. Showing you thought about it unprompted is the point.

**Confidentiality.** EDGAR filings only. Nothing from work — no CreditSights documents,
no internal prompts, no proprietary metrics, no customer data. Say this explicitly in
the first paragraph of your write-up. Legaltech and fintech interviewers notice when a
candidate draws that line without being asked.

---

## When something breaks

Copy the entire error message, paste it into Cursor's chat (Cmd+L), and ask what it means.
That is the intended use of the tool and it will resolve 90% of what you hit.

The four most common:

| Error | Cause |
|---|---|
| `command not found: python3` | Python not installed (Step 0) |
| `ModuleNotFoundError` | Forgot `source venv/bin/activate` |
| `authentication_error` / `API key not valid` | Key not set, or set in a different Terminal window |
| `429` / `RESOURCE_EXHAUSTED` | Free-tier rate limit. The scripts back off automatically; if it persists you've hit the daily cap — resume tomorrow (runs are resumable) |
| `FileNotFoundError: full_contract_txt` | Running from the wrong folder — `cd ~/contract-eval` |
