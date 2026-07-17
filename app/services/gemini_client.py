"""Gemini QA generation with structured-output validation and retries."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from app.config import settings

PROMPT_TEMPLATE = """You are a MedTech QA engineer writing test cases for a blood-pressure monitor.

Given the selected manual sections below, generate between 3 and 5 concrete QA test cases.

Rules:
- Base every test ONLY on the provided text. Do not invent device behavior.
- Each test must be executable by a human tester.
- Prefer safety-critical and measurement-related behaviors when present.
- Return ONLY valid JSON (no markdown fences, no commentary).

Required JSON schema:
{{
  "test_cases": [
    {{
      "id": "TC-001",
      "title": "short title",
      "precondition": "what must be true before starting",
      "steps": ["step 1", "step 2"],
      "expected_result": "observable expected outcome",
      "priority": "high|medium|low",
      "source_sections": ["3.2"]
    }}
  ]
}}

Selected sections:
{sections_block}
"""

REQUIRED_FIELDS = {"id", "title", "precondition", "steps", "expected_result", "priority"}


def _sections_block(sections: list[dict[str, Any]]) -> str:
    parts = []
    for s in sections:
        parts.append(
            f"### Section {s['section_number']}: {s['heading']}\n{s['body']}\n"
        )
    return "\n".join(parts)


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise ValueError("Model response did not contain JSON") from None
        return json.loads(match.group(0))


def validate_test_cases(payload: dict) -> list[dict]:
    if not isinstance(payload, dict) or "test_cases" not in payload:
        raise ValueError("JSON must contain a 'test_cases' array")
    cases = payload["test_cases"]
    if not isinstance(cases, list):
        raise ValueError("'test_cases' must be a list")
    if not 3 <= len(cases) <= 5:
        raise ValueError(f"Expected 3-5 test cases, got {len(cases)}")

    cleaned: list[dict] = []
    for i, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"test_cases[{i}] is not an object")
        missing = REQUIRED_FIELDS - set(case)
        if missing:
            raise ValueError(f"test_cases[{i}] missing fields: {sorted(missing)}")
        steps = case["steps"]
        if not isinstance(steps, list) or not steps or not all(isinstance(s, str) for s in steps):
            raise ValueError(f"test_cases[{i}].steps must be a non-empty string list")
        priority = str(case.get("priority", "medium")).lower()
        if priority not in {"high", "medium", "low"}:
            priority = "medium"
        source = case.get("source_sections") or []
        if not isinstance(source, list):
            source = []
        cleaned.append(
            {
                "id": str(case["id"]),
                "title": str(case["title"]).strip(),
                "precondition": str(case["precondition"]).strip(),
                "steps": [str(s).strip() for s in steps],
                "expected_result": str(case["expected_result"]).strip(),
                "priority": priority,
                "source_sections": [str(s) for s in source],
            }
        )
    return cleaned


def generate_qa_test_cases(sections: list[dict[str, Any]]) -> list[dict]:
    if not settings.gemini_api_key or settings.gemini_api_key.startswith("your_"):
        raise RuntimeError(
            "GEMINI_API_KEY is not configured. Copy .env.example to .env and set your key."
        )

    import google.generativeai as genai

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(settings.gemini_model)
    prompt = PROMPT_TEMPLATE.format(sections_block=_sections_block(sections))

    last_error: Exception | None = None
    attempts = settings.gemini_max_retries + 1
    for attempt in range(1, attempts + 1):
        try:
            response = model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.2,
                    "response_mime_type": "application/json",
                },
            )
            text = getattr(response, "text", None) or ""
            if not text and getattr(response, "candidates", None):
                # Fallback if .text is empty due to finish reasons
                parts = []
                for cand in response.candidates:
                    content = getattr(cand, "content", None)
                    if content and getattr(content, "parts", None):
                        for part in content.parts:
                            if hasattr(part, "text"):
                                parts.append(part.text)
                text = "\n".join(parts)
            if not text.strip():
                raise ValueError("Empty response from Gemini")
            parsed = _extract_json(text)
            return validate_test_cases(parsed)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < attempts:
                time.sleep(0.8 * attempt)
                continue
            break

    raise RuntimeError(f"Gemini generation failed after {attempts} attempts: {last_error}")
