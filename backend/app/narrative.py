"""AI-assisted summary using Groq (OpenAI-compatible API), grounded in retrieved passages (RAG).
The model only sees verified facts plus the retrieved reference passages, and its output is checked
afterwards. Without GROQ_API_KEY, or on any failure or rejection, the rule-based summary is used."""
import json, os, re

import httpx

URL = "https://api.groq.com/openai/v1/chat/completions"
SYSTEM = ("You draft the 'Summary and observations' section of a draft inspection report. "
          "Use ONLY the JSON facts and the reference passages provided. Do not invent causes, explanations, "
          "acceptance criteria or results. State every failing sample and every missing sample or attachment; "
          "do not soften a Fail. When you use a reference passage, cite it with its exact label in square "
          "brackets, for example [REF-01-test-method.md, §2.1]. Quote numbers exactly as given; never calculate averages, differences or any new number. Plain prose, at most 130 words, no headings.")


def _label(x):
    return f"{x['file']}, {x['ref']}"


_n = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())


def _fix_cites(text, passages):
    """Map loosely written citations ("[REF-01, §2.1]") onto the real retrieved label.
    A citation that matches no retrieved passage is left alone, so _grounded still rejects it."""
    def canon(m):
        c = m.group(1)
        mm = re.match(r"(.*?)[\s,;:]*([§¶].*)$", c)
        if mm:
            f, ref = _n(mm.group(1)), _n(mm.group(2))
            for x in passages:
                if _n(x["ref"]) == ref and f and _n(x["file"]).startswith(f):
                    return f"[{_label(x)}]"
        return m.group(0)
    return re.sub(r"\[([^\]]+)\]", canon, text)


def _grounded(text, facts, passages):
    labels = {_label(x) for x in passages}
    cites = re.findall(r"\[([^\]]+)\]", text)
    if any(c not in labels for c in cites):
        return False, "cited an unknown source"
    if labels and not cites:
        return False, "did not cite the retrieved context"
    for i in [f["id"] for f in facts["failing"]] + facts["missing_samples"] + facts["no_value"]:
        if i not in text:
            return False, "omitted a failure or gap"
    body = re.sub(r"\[[^\]]+\]", "", text)
    # compare numerically, so "2.6" matches the limit "2.60"
    num = lambda t: {round(float(n), 4) for n in re.findall(r"\d+(?:\.\d+)?", t)}
    allowed = {round(facts["min"], 4), round(facts["max"], 4)} | {round(m["value"], 4) for m in facts["measurements"]}
    for x in passages:
        allowed |= num(x["text"])
    bad = sorted(n for n in num(re.sub(r"S-\d+", "", body)) if "." in f"{n}" and n not in allowed)
    if bad:
        return False, f"contained an unverified number ({', '.join(map(str, bad))})"
    if re.search(r"\b(because|due to|caused|owing to|as a result of)\b", body, re.I):
        return False, "contained causal language"
    if not facts["calibration_supplied"] and "calibration" not in body.lower():
        return False, "omitted the missing calibration record"
    return True, ""


def draft_summary(p, A, has_pdf, use_ai):
    rules = A["summary"]
    key = (os.getenv("GROQ_API_KEY") or "").strip().strip("'\"")
    model = (os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b").strip().strip("'\"")   # empty or quoted value falls back
    if not (use_ai and key):
        return rules, "rules"
    facts = {"planned": len(p["samples"]), "measured": A["total"], "passed": A["passed"], "failed": A["failed"],
             "min": p["min"], "max": p["max"], "unit": p["unit"],
             "failing": [{"id": r["id"], "value": r["value"], "observation": r["observation"]} for r in A["rows"] if r["status"] == "fail"],
             "no_value": [r["id"] for r in A["rows"] if r["status"] == "missing"], "missing_samples": A["missing"],
             "calibration_supplied": has_pdf,
             "measurements": [{"id": r["id"], "value": r["value"]} for r in A["rows"] if r["value"] is not None]}
    context = [{"label": _label(x), "text": x["text"]} for x in A["passages"]]   # retrieved by engine.retrieve
    try:
        r = httpx.post(URL, headers={"Authorization": f"Bearer {key}"}, timeout=30, json={
            "model": model, "temperature": 0, "max_tokens": 2000,   # gpt-oss reasons first; leave room for the answer
            **({"reasoning_effort": "low"} if model.startswith("openai/gpt-oss") else {}),
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": json.dumps({"facts": facts, "reference_passages": context})}]})
        r.raise_for_status()
        text = (r.json()["choices"][0]["message"].get("content") or "").strip()
    except httpx.HTTPStatusError as e:  # Groq answered with an error: bad key, wrong model, rate limit
        return rules, f"rules (AI unavailable: Groq HTTP {e.response.status_code} for model '{model}': {e.response.text[:200]})"
    except Exception as e:  # network, timeout, malformed response
        return rules, f"rules (AI unavailable: {type(e).__name__}: {str(e)[:150]})"
    text = _fix_cites(text, A["passages"])
    ok, why = _grounded(text, facts, A["passages"])
    return (text, "ai") if ok else (rules, f"rules (AI draft rejected: {why})")