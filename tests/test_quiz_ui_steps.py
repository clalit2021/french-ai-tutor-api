import os
import sys

# Ensure demo mode (no API key)
os.environ["OPENAI_API_KEY"] = ""

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app import mimi

# Force demo path regardless of environment
mimi.openai_client = None
build_mimi_lesson = mimi.build_mimi_lesson


def test_quiz_adds_question_block_to_ui_steps():
    lesson = build_mimi_lesson()
    question_blocks = [s for s in lesson["ui_steps"] if s.get("type") == "question"]
    assert question_blocks, "Expected at least one question block in ui_steps"
