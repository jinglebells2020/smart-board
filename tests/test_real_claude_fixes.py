"""Two bugs found when testing with the real Claude.

1. The second message failed with "400 Invalid signature in thinking
   block": the kept history sent Claude's old thinking blocks back, but
   the notebook (memory) changes the start of the conversation every
   turn. _without_thinking() leaves them out of the history.
2. The detective crashed when Claude added advice after its question.
   Now only the text up to the first "?" is kept, and a rejected answer
   is retried (ToolStrategy) instead of raising.
"""

import unittest

from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError

from agent.build import _without_thinking
from agent.detective import DetectiveAnswer, detective_spec


class TestDetectiveQuestions(unittest.TestCase):
    def test_long_answer_is_trimmed_to_its_first_question(self):
        long = ("Could the resistor between row 5 and row 10 be a different value than "
                "220 ohms? Check its color bands. They should be red, red, brown.")
        answer = DetectiveAnswer(questions=[long])
        self.assertEqual(answer.questions, [
            "Could the resistor between row 5 and row 10 be a different value than 220 ohms?"])

    def test_no_question_mark_still_rejected(self):
        with self.assertRaises(ValidationError):
            DetectiveAnswer(questions=["Check the resistor."])

    def test_rejected_answers_are_retried(self):
        self.assertIsInstance(detective_spec(session=None)["response_format"], ToolStrategy)


class TestWithoutThinking(unittest.TestCase):
    def test_thinking_blocks_are_removed(self):
        text = {"type": "text", "text": "Look at row 10."}
        original = AIMessage(content=[
            {"type": "thinking", "thinking": "The LED...", "signature": "abc"},
            {"type": "redacted_thinking", "data": "xyz"},
            text,
        ])
        before = list(original.content)
        cleaned = _without_thinking(original)
        self.assertEqual(cleaned.content, [text])
        self.assertIsNot(cleaned, original)
        self.assertEqual(original.content, before)  # the original is not changed

    def test_other_messages_come_back_unchanged(self):
        for message in (HumanMessage(content="Check my circuit"),
                        AIMessage(content="Your circuit is correct!"),
                        AIMessage(content=[{"type": "text", "text": "Hi"}])):
            with self.subTest(message=message):
                self.assertIs(_without_thinking(message), message)


if __name__ == "__main__":
    unittest.main()
