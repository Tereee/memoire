"""
Controle rejouable de la lecture de stop_details.

L'API ne renseigne stop_details que sur stop_reason == "refusal". Une
troncature (max_tokens) laisse donc stop_details_category a None : ce test
distingue "aucun refus" de "champ mal lu" sans appel reseau, en faisant
traverser Runner.call() par une reponse construite avec le type reel du SDK.

    python -m unittest tests.test_stop_details
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-dummy-offline")

from anthropic.types import Message  # noqa: E402

from src.providers import Runner  # noqa: E402


def _message(stop_reason: str, stop_details: dict | None) -> Message:
    return Message.model_validate({
        "id": "msg_test", "type": "message", "role": "assistant",
        "model": "claude-sonnet-5", "content": [],
        "stop_reason": stop_reason, "stop_details": stop_details,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 0},
    })


class StopDetailsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.runner = Runner(run_id="test_stop_details", out_dir=Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _call(self, msg: Message):
        self.runner.client.messages.create = lambda **kw: msg
        return self.runner.call(alias="sonnet", user_prompt="x", prompt_id="mock", condition="test")

    def test_refusal_fills_category(self) -> None:
        rec = self._call(_message("refusal", {"type": "refusal", "category": "cyber", "explanation": "t"}))
        self.assertEqual(rec.stop_reason, "refusal")
        self.assertEqual(rec.stop_details_category, "cyber")

    def test_refusal_without_category_is_none(self) -> None:
        rec = self._call(_message("refusal", {"type": "refusal", "category": None, "explanation": "t"}))
        self.assertEqual(rec.stop_reason, "refusal")
        self.assertIsNone(rec.stop_details_category)

    def test_truncation_has_no_category(self) -> None:
        rec = self._call(_message("max_tokens", None))
        self.assertEqual(rec.stop_reason, "max_tokens")
        self.assertIsNone(rec.stop_details_category)

    def test_category_is_written_to_jsonl(self) -> None:
        self._call(_message("refusal", {"type": "refusal", "category": "bio", "explanation": "t"}))
        row = json.loads(self.runner.path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(row["stop_details_category"], "bio")


if __name__ == "__main__":
    unittest.main()
