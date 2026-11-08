"""Optional live DeepSeek smoke test.

Run explicitly with RUN_LIVE_AI_TESTS=1 pytest -q tests/test_deepseek_coach.py.
Normal CI must never require paid API credentials or make external AI calls.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_AI_TESTS") != "1",
    reason="Live AI smoke test is opt-in; set RUN_LIVE_AI_TESTS=1 to run.",
)


def test_deepseek_assessment_live():
    from app.ai_assessment import assess_business_email

    result = assess_business_email(
        assignment=(
            "Write a professional email to a university professor. "
            "Ask for a meeting to discuss an internship opportunity."
        ),
        submission=(
            "Dear Professor Smith,\n\n"
            "I am writing to ask if you have time to meet with me "
            "to discuss an internship opportunity.\n\n"
            "I am very interested in your research and would appreciate "
            "the chance to learn more about your work.\n\n"
            "Please let me know if you are available next week.\n\n"
            "Best regards,\nAlex"
        ),
        rubric={
            "task_completion": "Does the email fulfill the assignment?",
            "professional_tone": "Is the tone polite and professional?",
            "structure_clarity": "Is the email well organized?",
            "grammar_vocabulary": "Is the English accurate and appropriate?",
        },
        pass_threshold=80,
    )
    assert 0 <= result["score"] <= 100
    assert isinstance(result["passed"], bool)
    assert result["dimensions"]
