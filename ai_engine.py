"""
AI engine for FactoryBrain.

Two jobs:
  1. extract_knowledge(text)  -> structured fields from free text
  2. ask_ai(question, context) -> answer built only from stored knowledge

Both require a working OPENAI_API_KEY. If the key is missing or the OpenAI
call fails, they raise AIError so the caller can show a real error message
instead of silently returning fake data.
"""

import json
import re

from config import Config

# Lazy client so the app still imports even if openai isn't installed yet.
_client = None
_SEVERITY_LEVELS = ["Low", "Medium", "High", "Critical"]


class AIError(Exception):
    """Raised when the OpenAI call cannot be completed."""


def _get_client():
    global _client
    if _client is not None:
        return _client
    if not Config.OPENAI_API_KEY:
        return None
    try:
        from openai import OpenAI
        _client = OpenAI(api_key=Config.OPENAI_API_KEY)
        return _client
    except Exception as e:  # noqa
        print(f"[ai_engine] Could not init OpenAI client: {e}")
        return None


def ai_status():
    return {"ready": bool(Config.OPENAI_API_KEY), "model": Config.OPENAI_MODEL}


# --------------------------------------------------------------------------- #
#  1. Extraction
# --------------------------------------------------------------------------- #
_EXTRACT_SYSTEM = (
    "You are a senior industrial-maintenance reliability engineer extracting structured "
    "knowledge from a worker's free-text note about a fault and its fix. Return a STRICT "
    "JSON object with EXACTLY these seven keys and nothing else:\n"
    "\n"
    '  "equipment_name": The specific machine/equipment. Include the unit, line, or asset '
    "number if the worker mentioned one (e.g. \"Hydraulic Press - Line 2\"). Use Title Case. "
    "Do NOT invent an identifier that was not stated.\n"
    "\n"
    '  "problem_type": A specific technical fault CATEGORY that names the failing component '
    "or failure mode — NOT a vague symptom. Prefer e.g. \"Hydraulic Seal Failure\" over "
    "\"pressure loss\", \"Cooling Fan Blockage\" over \"overheating\". 2-5 words.\n"
    "\n"
    '  "problem_cause": The concrete root cause in one or two sentences. Name the failed '
    "part, the physical mechanism, AND the resulting consequence (part -> mechanism -> "
    "effect). Do NOT merely restate the symptom.\n"
    "\n"
    '  "solution_steps": An ARRAY of concrete, ordered, actionable steps (strings). Begin '
    "with any prep/safety step the worker took or that the action clearly required (e.g. "
    "lockout/tagout, depressurize, power down), then the repair actions, and END with a "
    "verification step confirming the fix held. Every step must have a clear object — never "
    "a bare verb like \"fix\" or \"check\" with nothing specified.\n"
    "\n"
    '  "severity": EXACTLY one of "Low","Medium","High","Critical", chosen with this rubric, '
    "picking the HIGHEST level that applies:\n"
    "     - Critical: a safety hazard (fire, injury risk, leak of hazardous material) OR a "
    "full production-line stoppage.\n"
    "     - High: this machine is down/stopped, but there is no safety risk and the rest of "
    "the line keeps running.\n"
    "     - Medium: the machine still runs but is degraded, slowed, or at risk.\n"
    "     - Low: minor or cosmetic issue with no functional impact.\n"
    "\n"
    '  "keywords": An ARRAY of 4-7 SPECIFIC technical search terms (components, fault modes, '
    "symptoms, the equipment). Avoid generic filler like \"repair\", \"fix\", \"problem\".\n"
    "\n"
    '  "prevention": One short, practical recommendation to stop THIS fault from recurring, '
    "derived directly from the identified root cause (e.g. a scheduled inspection, cleaning, "
    "lubrication, or part-replacement interval for the component that failed). One sentence. "
    "If the text gives no reasonable basis for a prevention tip, use an empty string \"\".\n"
    "\n"
    "CRITICAL CONSTRAINTS:\n"
    "  - Respond with JSON ONLY — no markdown, no code fences, no commentary.\n"
    "  - Detect the input language (Arabic or English) and write ALL values, INCLUDING the "
    "keywords, in that SAME language. Never mix languages.\n"
    "  - Use ONLY facts present in the worker's text. Do NOT invent part numbers, torque "
    "specs, measurements, or steps that were not mentioned. \"Specific\" means precise about "
    "what was actually said — not fabricated detail."
)


def extract_knowledge(text: str) -> dict:
    client = _get_client()
    if client is None:
        raise AIError("AI is not configured: set a valid OPENAI_API_KEY in your .env file.")

    try:
        resp = client.chat.completions.create(
            model=Config.OPENAI_MODEL,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": text},
            ],
        )
        raw = resp.choices[0].message.content
        data = json.loads(raw)
        return _normalize(data, text)
    except Exception as e:  # noqa
        print(f"[ai_engine] extract error: {e}")
        raise AIError(f"AI extraction failed: {e}")


def _normalize(data: dict, raw: str) -> dict:
    sev = str(data.get("severity", "Medium")).capitalize()
    if sev not in _SEVERITY_LEVELS:
        sev = "Medium"
    steps = data.get("solution_steps", [])
    if isinstance(steps, str):
        steps = [s.strip() for s in re.split(r"[\n;]+", steps) if s.strip()]
    kws = data.get("keywords", [])
    if isinstance(kws, str):
        kws = [k.strip() for k in re.split(r"[,،\n]+", kws) if k.strip()]
    return {
        "equipment_name": (data.get("equipment_name") or "Unknown").strip(),
        "problem_type": (data.get("problem_type") or "").strip(),
        "problem_cause": (data.get("problem_cause") or "").strip(),
        "solution_steps": steps or [],
        "severity": sev,
        "keywords": kws or [],
        "prevention": (data.get("prevention") or "").strip(),
        "raw_text": raw,
    }


# --------------------------------------------------------------------------- #
#  2. Q&A
# --------------------------------------------------------------------------- #
_ASK_SYSTEM = (
    "You are FactoryBrain, an assistant that helps factory employees solve equipment "
    "problems using ONLY the captured knowledge provided below. "
    "Give a clear, practical answer with concrete steps. "
    "If the provided knowledge does not cover the question, say so honestly and suggest "
    "the closest related entry. Reply in the same language as the question (Arabic or English). "
    "Keep it concise and actionable."
)


def ask_ai(question: str, context: list) -> str:
    client = _get_client()
    if client is None:
        raise AIError("AI is not configured: set a valid OPENAI_API_KEY in your .env file.")

    context_text = _format_context(context)
    try:
        resp = client.chat.completions.create(
            model=Config.OPENAI_MODEL,
            temperature=0.3,
            messages=[
                {"role": "system", "content": _ASK_SYSTEM},
                {"role": "user", "content":
                    f"CAPTURED KNOWLEDGE:\n{context_text}\n\nQUESTION:\n{question}"},
            ],
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:  # noqa
        print(f"[ai_engine] ask error: {e}")
        raise AIError(f"AI request failed: {e}")


def _format_context(context: list) -> str:
    if not context:
        return "(no knowledge entries available)"
    blocks = []
    for i, c in enumerate(context, 1):
        steps = "\n   ".join(f"{j}. {s}" for j, s in enumerate(c.get("solution_steps", []), 1))
        prevention = c.get("prevention")
        block = (
            f"[{i}] Equipment: {c.get('equipment_name')}\n"
            f"   Problem: {c.get('problem_type')}\n"
            f"   Cause: {c.get('problem_cause')}\n"
            f"   Severity: {c.get('severity')}\n"
            f"   Solution steps:\n   {steps}"
        )
        if prevention:
            block += f"\n   Prevention: {prevention}"
        blocks.append(block)
    return "\n\n".join(blocks)
