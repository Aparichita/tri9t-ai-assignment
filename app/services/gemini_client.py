"""LLM QA generation with structured-output validation and retries (Groq provider)."""

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


def _is_retryable(exc: Exception) -> bool:
    try:
        from groq import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

        if isinstance(exc, (RateLimitError, APITimeoutError, APIConnectionError)):
            return True
        if isinstance(exc, APIStatusError) and exc.status_code in {408, 429, 500, 502, 503, 504}:
            return True
    except ImportError:
        pass
    return False


def generate_qa_test_cases(sections: list[dict[str, Any]]) -> list[dict]:
    if not settings.groq_api_key or settings.groq_api_key.startswith("your_"):
        raise RuntimeError(
            "GROQ_API_KEY is not configured. Copy .env.example to .env and set your key."
        )

    from groq import Groq

    client = Groq(api_key=settings.groq_api_key, timeout=settings.groq_timeout_seconds)
    prompt = PROMPT_TEMPLATE.format(sections_block=_sections_block(sections))

    last_error: Exception | None = None
    attempts = settings.groq_max_retries + 1
    for attempt in range(1, attempts + 1):
        try:
            response = client.chat.completions.create(
                model=settings.groq_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            choice = response.choices[0] if response.choices else None
            text = (choice.message.content if choice and choice.message else None) or ""
            if not text.strip():
                raise ValueError("Empty response from LLM")
            parsed = _extract_json(text)
            return validate_test_cases(parsed)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < attempts and _is_retryable(exc):
                time.sleep(0.8 * attempt)
                continue
            if attempt < attempts and not _is_retryable(exc):
                # Validation / parse errors may succeed on retry with a fresh completion
                if isinstance(exc, (ValueError, json.JSONDecodeError)):
                    time.sleep(0.8 * attempt)
                    continue
            break

    raise RuntimeError(f"LLM generation failed after {attempts} attempts: {last_error}")
