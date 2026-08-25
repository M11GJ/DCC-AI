#!/usr/bin/env python3
"""Unit tests for DCC AI Pipe model exposure and Local 80B policy."""

import asyncio
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dccai_pipe import Pipe


class DccAiPipeTests(unittest.TestCase):
    def setUp(self):
        self.pipe = Pipe()
        self.pipe.valves.LITELLM_API_KEY = "test-key"
        self.pipe.valves.MONTHLY_TOKEN_LIMIT = 0

    def test_local_model_is_exposed_with_text_only_metadata(self):
        models = {model["id"]: model for model in self.pipe.pipes()}
        local = models["dccai-local-80b"]
        self.assertEqual(local["name"], "DCC AI Local 80B")
        self.assertFalse(local["meta"]["vision"])
        self.assertEqual(local["meta"]["knowledge"], [])
        self.assertTrue(local["meta"]["capabilities"]["web_search"])
        self.assertTrue(local["meta"]["capabilities"]["builtin_tools"])
        self.assertEqual(self.pipe.valves.LOCAL_MAX_CONCURRENCY, 1)

    def test_local_model_is_rejected_for_chat_api_calls(self):
        result = asyncio.run(
            self.pipe.pipe(
                {
                    "model": "dccai.dccai-local-80b",
                    "messages": [{"role": "user", "content": "hello"}],
                },
                __user__={"id": "test-user"},
                __metadata__={},
            )
        )
        self.assertIn("Webチャット画面からのみ", result)

    def test_local_model_rejects_image_input_before_upstream(self):
        result = asyncio.run(
            self.pipe.pipe(
                {
                    "model": "dccai.dccai-local-80b",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "what is this"},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": "data:image/png;base64,AAAA"},
                                },
                            ],
                        }
                    ],
                },
                __user__={"id": "test-user"},
                __metadata__={"chat_id": "chat-test"},
            )
        )
        self.assertIn("画像入力に対応していません", result)

    def test_unknown_model_does_not_fall_back_to_low(self):
        for model in ("unknown-model", "evil.dccai-low", "evil.dccai-local-80b"):
            with self.subTest(model=model):
                result = asyncio.run(
                    self.pipe.pipe(
                        {"model": model, "messages": []},
                        __user__={"id": "test-user"},
                        __metadata__={"chat_id": "chat-test"},
                    )
                )
                self.assertIn("利用できません", result)


if __name__ == "__main__":
    unittest.main()
