"""AI-assisted summary using Groq (OpenAI-compatible API), grounded in retrieved passages (RAG).
The model only sees verified facts plus the retrieved reference passages, and its output is checked
afterwards. Without GROQ_API_KEY, or on any failure or rejection, the rule-based summary is used."""
import json, os, re

import httpx

URL = "https://api.groq.com/openai/v1/chat/completions"
SYSTEM = ("You draft the 'Summary and observations' section of a draft inspection report. "
          "Use ONLY the JSON facts and the reference passages provided. Do not invent causes, explanations, "
          "acceptance criteria or results. State every failing sample listed in the facts, the total failing count, and every missing sample or attachment; "
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
    for i in [f["id"] for f in facts["failing"]] + facts["missing_samples"][:10] + facts["no_value"][:10]:
        if i not in text:
            return False, "omitted a failure or gap"
    body = re.sub(r"\[[^\]]+\]", "", text)
    # compare numerically, so "2.6" matches the limit "2.60"
    num = lambda t: {round(float(n), 4) for n in re.findall(r"\d+(?:\.\d+)?", t)}
    allowed = {round(facts["min"], 4), round(facts["max"], 4)} | set(facts["_all"])
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
             "failing": [{"id": r["id"], "value": r["value"], "observation": r["observation"]} for r in A["rows"] if r["status"] == "fail"][:10],
             "failing_total": A["failed"],
             "no_value": [r["id"] for r in A["rows"] if r["status"] == "missing"], "missing_samples": A["missing"],
             "calibration_supplied": has_pdf,
             "measurements": [{"id": r["id"], "value": r["value"]} for r in A["rows"] if r["value"] is not None][:50],
             "_all": [round(r["value"], 4) for r in A["rows"] if r["value"] is not None]}   # not sent to the model
    context = [{"label": _label(x), "text": x["text"]} for x in A["passages"]]   # retrieved by engine.retrieve
    try:
        r = httpx.post(URL, headers={"Authorization": f"Bearer {key}"}, timeout=30, json={
            "model": model, "temperature": 0, "max_tokens": 2000,   # gpt-oss reasons first; leave room for the answer
            **({"reasoning_effort": "low"} if model.startswith("openai/gpt-oss") else {}),
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": json.dumps({"facts": {k: v for k, v in facts.items() if not k.startswith("_")}, "reference_passages": context})}]})
        r.raise_for_status()
        text = (r.json()["choices"][0]["message"].get("content") or "").strip()
    except httpx.HTTPStatusError as e:  # Groq answered with an error: bad key, wrong model, rate limit
        return rules, f"rules (AI unavailable: Groq HTTP {e.response.status_code} for model '{model}': {e.response.text[:200]})"
    except Exception as e:  # network, timeout, malformed response
        return rules, f"rules (AI unavailable: {type(e).__name__}: {str(e)[:150]})"
    text = _fix_cites(text, A["passages"])
    ok, why = _grounded(text, facts, A["passages"])
    return (text, "ai") if ok else (rules, f"rules (AI draft rejected: {why})")


# ---------- results dataset summary (for the report, instead of printing every row) ----------
SYSTEM_RESULTS = ("You write a short 'Results overview' paragraph for a draft inspection report. The full dataset is attached "
                  "separately, so the paragraph must summarise it. Use ONLY the JSON facts provided. Quote numbers exactly as given; "
                  "never calculate percentages, averages or differences. Name every failing sample listed. Do not invent causes or "
                  "explanations. Plain prose, at most 90 words, no headings, no bullet points.")


def result_stats(p, A):
    """Deterministic statistics. Computed in code, never by the model."""
    vals = [r["value"] for r in A["rows"] if r["value"] is not None]
    obs = {}
    for r in A["rows"]:
        if r["observation"]:
            obs[r["observation"]] = obs.get(r["observation"], 0) + 1
    return {"recorded": A["total"], "planned": len(p["samples"]), "passed": A["passed"], "failed": A["failed"],
            "no_value": A["no_value"], "min": min(vals) if vals else None, "max": max(vals) if vals else None,
            "mean": round(sum(vals) / len(vals), 3) if vals else None, "unit": p["unit"],
            "range": A["range"], "missing_samples": A["missing"],
            "failing": [{"id": r["id"], "value": r["value"]} for r in A["rows"] if r["status"] == "fail"][:10],
            "failing_total": A["failed"],
            "top_observations": sorted(obs.items(), key=lambda kv: -kv[1])[:3]}


def _rule_results_summary(s):
    t = f"{s['recorded']} results were recorded against {s['planned']} planned samples: {s['passed']} passed and {s['failed']} failed"
    t += f", and {s['no_value']} had no value." if s["no_value"] else "."
    if s["min"] is not None:
        t += f" Measured values ranged from {s['min']:.2f} to {s['max']:.2f} {s['unit']} (mean {s['mean']:.2f}) against the acceptance range of {s['range']}."
    if s["failing"]:
        t += " Failing: " + ", ".join(f"{f['id']} ({f['value']:.2f})" for f in s["failing"])
        t += f" and {s['failing_total'] - len(s['failing'])} more." if s["failing_total"] > len(s["failing"]) else "."
    if s["missing_samples"]:
        t += " No result supplied for " + ", ".join(s["missing_samples"]) + "."
    if s["top_observations"] and len(s["top_observations"]) > 1:
        t += " Most common observations: " + ", ".join(f"{o} ({c})" for o, c in s["top_observations"]) + "."
    return t


def summarize_results(p, A, use_ai):
    """(text, source). Falls back to the rule-based text on any failure or rejection."""
    s = result_stats(p, A)
    rules = _rule_results_summary(s)
    key = (os.getenv("GROQ_API_KEY") or "").strip().strip("'\"")
    model = (os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b").strip().strip("'\"")
    if not (use_ai and key):
        return rules, "rules"
    try:
        r = httpx.post(URL, headers={"Authorization": f"Bearer {key}"}, timeout=30, json={
            "model": model, "temperature": 0, "max_tokens": 1500,
            **({"reasoning_effort": "low"} if model.startswith("openai/gpt-oss") else {}),
            "messages": [{"role": "system", "content": SYSTEM_RESULTS}, {"role": "user", "content": json.dumps({"facts": s})}]})
        r.raise_for_status()
        text = (r.json()["choices"][0]["message"].get("content") or "").strip()
    except Exception as e:
        return rules, f"rules (AI unavailable: {type(e).__name__})"
    num = lambda t: {round(float(x), 4) for x in re.findall(r"\d+(?:\.\d+)?", t)}
    allowed = num(json.dumps({k: v for k, v in s.items() if k != "top_observations"})) | num(s["range"])
    bad = sorted(x for x in num(re.sub(r"S-\d+", "", text)) if x not in allowed and "." in f"{x}")
    if not text:
        return rules, "rules (AI returned nothing)"
    if bad:
        return rules, f"rules (AI draft rejected: unverified number {bad})"
    if any(f["id"] not in text for f in s["failing"]):
        return rules, "rules (AI draft rejected: omitted a failing sample)"
    if re.search(r"\b(because|due to|caused|owing to|as a result of)\b", text, re.I):
        return rules, "rules (AI draft rejected: causal language)"
    return text, "ai"
