from app.services.gemini_client import validate_test_cases, _extract_json
import pytest


def test_validate_accepts_three_cases():
    payload = {
        "test_cases": [
            {
                "id": "TC-001",
                "title": "t1",
                "precondition": "p",
                "steps": ["s1"],
                "expected_result": "e",
                "priority": "high",
                "source_sections": ["3.2"],
            },
            {
                "id": "TC-002",
                "title": "t2",
                "precondition": "p",
                "steps": ["s1"],
                "expected_result": "e",
                "priority": "medium",
            },
            {
                "id": "TC-003",
                "title": "t3",
                "precondition": "p",
                "steps": ["s1"],
                "expected_result": "e",
                "priority": "low",
            },
        ]
    }
    cases = validate_test_cases(payload)
    assert len(cases) == 3


def test_validate_rejects_too_few():
    with pytest.raises(ValueError, match="3-5"):
        validate_test_cases({"test_cases": []})


def test_extract_json_from_fenced_block():
    text = '```json\n{"test_cases": []}\n```'
    data = _extract_json(text)
    assert "test_cases" in data
