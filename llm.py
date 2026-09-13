"""
llm.py — one call interface for Gemini and Anthropic, using only the standard library

No pip install required. No SDKs. Just urllib, which ships with Python.

Setup — Gemini free tier (no credit card):
    1. https://aistudio.google.com/apikey  ->  Create API key
    2. export GEMINI_API_KEY='your-key'

Setup — Anthropic (paid):
    export ANTHROPIC_API_KEY='sk-ant-...'

Free-tier rate limits are enforced client-side below. Without throttling you WILL
get 429s partway through a run.
"""

import json
import os
import time
import urllib.error
import urllib.request

# --- free-tier limits, conservative on purpose ------------------------------------
# Google publishes ~10-15 RPM and ~250k TPM for Flash-class models on the free tier,
# but the practical ceiling varies by region and account. Verify at
# https://ai.google.dev/gemini-api/docs/rate-limits and raise these if billing is on.
FREE_TIER_RPM = 6
FREE_TIER_TPM = 100_000

# Google rotates model IDs faster than any doc keeps up with. If a call 404s, run
#   python3 llm.py --list
# to ask the API what it actually serves today, then update these.
#
# IMPORTANT: free-tier daily quotas are PER MODEL. Exhausting one model's daily
# allowance does not touch another's — switching models unblocks you immediately.
# But never mix models within a single results file: an eval whose rows came from
# different models measures nothing. Finish an arm on one model, or restart it.
MODELS = {
    # alias -> (provider, model_id)
    "lite":              ("gemini", "gemini-3.1-flash-lite"),   # highest daily quota
    "lite-25":           ("gemini", "gemini-2.5-flash-lite"),   # fallback
    "gemini-flash":      ("gemini", "gemini-3.6-flash"),
    "gemini-flash-lite": ("gemini", "gemini-3.1-flash-lite"),
    "sonnet":            ("anthropic", "claude-sonnet-5"),
    "haiku":             ("anthropic", "claude-haiku-4-5-20251001"),
    "opus":              ("anthropic", "claude-opus-5"),
}

DEFAULT_MODEL = "lite"

_call_times = []      # timestamps, for RPM throttle
_token_window = []    # (timestamp, tokens), for TPM throttle


def resolve(alias):
    """Turn a friendly alias into (provider, model_id). Raw model IDs pass through."""
    if alias in MODELS:
        return MODELS[alias]
    if alias.startswith("gemini"):
        return ("gemini", alias)
    if alias.startswith("claude"):
        return ("anthropic", alias)
    raise ValueError(f"Unknown model '{alias}'. Known aliases: {list(MODELS)}")


def _require_key(provider):
    if provider == "gemini":
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise SystemExit(
                "GEMINI_API_KEY not set.\n"
                "  Free key: https://aistudio.google.com/apikey\n"
                "  Then:     export GEMINI_API_KEY='your-key'"
            )
        return key
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit(
            "ANTHROPIC_API_KEY not set.\n"
            "  Get one at https://console.anthropic.com\n"
            "  Then:       export ANTHROPIC_API_KEY='sk-ant-...'"
        )
    return key


def list_gemini_models():
    """Ask the API which models exist right now. The only reliable source."""
    key = _require_key("gemini")
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
        headers={"x-goog-api-key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:400]}")
    out = []
    for m in data.get("models", []):
        if "generateContent" in (m.get("supportedGenerationMethods") or []):
            out.append((m["name"].replace("models/", ""),
                        m.get("inputTokenLimit", 0)))
    return sorted(out)


def _post(url, headers, payload, timeout=180):
    """POST JSON, return parsed JSON. Raises RuntimeError with the HTTP body on error."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:4000]
        raise RuntimeError(f"HTTP {e.code}: {body}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e.reason}") from None


def _throttle(est_tokens, verbose=True):
    """Sleep as needed to stay under free-tier RPM and TPM."""
    now = time.time()
    _call_times[:] = [t for t in _call_times if now - t < 60]
    _token_window[:] = [(t, n) for t, n in _token_window if now - t < 60]

    waits = []
    if len(_call_times) >= FREE_TIER_RPM:
        waits.append(60 - (now - _call_times[0]) + 0.5)
    if _token_window and sum(n for _, n in _token_window) + est_tokens > FREE_TIER_TPM:
        waits.append(60 - (now - _token_window[0][0]) + 0.5)

    wait = max(waits) if waits else 0
    if wait > 0:
        if verbose:
            print(f"      (rate limit — waiting {wait:.0f}s)")
        time.sleep(wait)

    _call_times.append(time.time())
    _token_window.append((time.time(), est_tokens))


_consecutive_failures = [0]


def call(prompt, model=DEFAULT_MODEL, max_tokens=1000, retries=3, throttle=True):
    """Send one prompt. Returns (text, input_tokens, output_tokens)."""
    if _consecutive_failures[0] >= 3:
        raise SystemExit(
            "\nStopping: 3 documents failed in a row on quota errors.\n"
            "Grinding through the rest wastes time and tells you nothing new.\n"
            "Progress is saved — re-run the same command to resume.\n"
            "Diagnose with:  python3 llm.py --list   then   python3 llm.py"
        )
    provider, model_id = resolve(model)
    key = _require_key(provider)
    est = len(prompt) // 4  # ~4 chars per token

    last_err = None
    for attempt in range(retries):
        if throttle and provider == "gemini":
            _throttle(est)
        try:
            if provider == "gemini":
                url = ("https://generativelanguage.googleapis.com/v1beta/models/"
                       f"{model_id}:generateContent")
                resp = _post(
                    url,
                    {"content-type": "application/json", "x-goog-api-key": key},
                    {"contents": [{"parts": [{"text": prompt}]}],
                     "generationConfig": {"maxOutputTokens": max_tokens,
                                          "temperature": 0}},
                )
                cands = resp.get("candidates") or []
                if not cands:
                    # Usually a safety block or an empty finish. Surface it rather
                    # than silently returning "" — it's a real failure mode to count.
                    raise RuntimeError(f"No candidates returned: {str(resp)[:300]}")
                parts = cands[0].get("content", {}).get("parts") or []
                text = "".join(p.get("text", "") for p in parts)
                um = resp.get("usageMetadata", {})
                _consecutive_failures[0] = 0
                return (text,
                        um.get("promptTokenCount", est),
                        um.get("candidatesTokenCount", 0))

            resp = _post(
                "https://api.anthropic.com/v1/messages",
                {"content-type": "application/json",
                 "x-api-key": key,
                 "anthropic-version": "2023-06-01"},
                {"model": model_id, "max_tokens": max_tokens,
                 "messages": [{"role": "user", "content": prompt}]},
            )
            text = "".join(b.get("text", "") for b in resp.get("content", [])
                           if b.get("type") == "text")
            usage = resp.get("usage", {})
            _consecutive_failures[0] = 0
            return text, usage.get("input_tokens", est), usage.get("output_tokens", 0)

        except Exception as e:
            last_err = e
            msg = str(e).lower()
            if "404" in msg or "not_found" in msg:
                print(f"\n  Model '{model_id}' is not available.")
                if provider == "gemini":
                    try:
                        print("  Currently available with a free-tier key:")
                        for name, lim in list_gemini_models():
                            if "flash" in name and "preview" not in name:
                                print(f"    {name}   (context {lim:,})")
                        print("\n  Update the MODELS dict at the top of llm.py,"
                              "\n  or pass --model <id> directly.")
                    except Exception as le:
                        print(f"  (could not list models: {le})")
                raise
            if any(k in msg for k in ("429", "rate", "quota", "resource_exhausted",
                                      "overloaded", "529", "503")):
                # Surface WHICH quota. "PerDay" means waiting won't help today;
                # "PerMinute" means the request is simply too big or too frequent.
                detail = ""
                for marker in ("PerDay", "PerMinute", "InputTokenCount",
                               "RequestsPerDay", "retryDelay"):
                    if marker.lower() in msg:
                        detail = f" [{marker}]"
                        break
                backoff = 45 * (attempt + 1)
                print(f"      (rate limited{detail} — backing off {backoff}s)")
                if "perday" in msg.replace(" ", "").lower():
                    print("      NOTE: this looks like a DAILY quota. Waiting won't")
                    print("      help until it resets. Re-run tomorrow — progress is saved.")
                time.sleep(backoff)
            elif attempt < retries - 1:
                time.sleep(2 ** attempt * 3)

    _consecutive_failures[0] += 1
    print("\n" + "=" * 66)
    print("ALL RETRIES FAILED — full error below")
    print("=" * 66)
    print(last_err)
    print("=" * 66)
    print("Look for 'quotaMetric' / 'quotaId' in that text:")
    print("  ...PerDay          -> daily cap. Resume tomorrow; progress is saved.")
    print("  ...PerMinute       -> too frequent. Lower FREE_TIER_RPM in llm.py.")
    print("  ...FreeTier        -> this model has no free quota. Try another:")
    print("                        python3 llm.py --list")
    print("                        python3 01_extract.py --model <other-model>")
    raise last_err


# Rough $/million tokens. Gemini free tier is $0 within quota.
# Verify: https://ai.google.dev/pricing
#         https://docs.claude.com/en/docs/about-claude/models
RATES = {
    "gemini-3.1-flash-lite":      {"in": 0.00, "out": 0.00, "note": "free tier"},
    "gemini-3.6-flash":           {"in": 0.00, "out": 0.00, "note": "free tier"},
    "gemini-2.5-flash":           {"in": 0.00, "out": 0.00, "note": "free tier"},
    "gemini-2.5-flash-lite":      {"in": 0.00, "out": 0.00, "note": "free tier"},
    "claude-sonnet-5":            {"in": 3.00, "out": 15.00, "note": ""},
    "claude-opus-5":              {"in": 5.00, "out": 25.00, "note": ""},
    "claude-haiku-4-5-20251001":  {"in": 1.00, "out": 5.00, "note": ""},
}


def cost_estimate(model, in_tokens, out_tokens):
    _, model_id = resolve(model)
    r = RATES.get(model_id)
    if not r:
        return None, ""
    return in_tokens / 1e6 * r["in"] + out_tokens / 1e6 * r["out"], r["note"]


if __name__ == "__main__":
    # python3 llm.py           -> connectivity smoke test
    # python3 llm.py --list    -> what models does my key actually serve?
    # python3 llm.py <model>   -> test a specific model
    import sys

    if "--list" in sys.argv:
        print("Gemini models available to your key (generateContent):\n")
        try:
            for name, lim in list_gemini_models():
                print(f"  {name:42s} context {lim:,}")
        except Exception as e:
            print(f"  FAILED: {e}")
        sys.exit(0)

    m = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL
    p, mid = resolve(m)
    print(f"Testing {mid} ({p})...")
    try:
        text, i, o = call("Reply with exactly the word: ok", model=m, max_tokens=10)
        print(f"  response: {text.strip()!r}")
        print(f"  tokens:   in={i} out={o}")
        print("  WORKS")
    except Exception as e:
        print(f"  FAILED: {e}")
