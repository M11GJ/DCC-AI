import copy
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import responses_gateway as gateway


FUNCTION = {
    "name": "get_weather",
    "description": "Get weather.",
    "parameters": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
        "additionalProperties": False,
    },
}


class ResponsesGatewayTests(unittest.TestCase):
    def test_decisions_model_resolution_uses_an_explicit_allowlist(self):
        for model in (
            "jev-latest",
            "dccai-jev-latest",
            "typesafe/jev-latest",
            "~typesafe/jev-latest",
        ):
            with self.subTest(model=model):
                self.assertEqual(
                    gateway.resolve_decisions_model(model),
                    gateway.OPENROUTER_JEV_MODEL,
                )

        for model in ("typesafe/jev-1.13", "openai/gpt-6-astra", ""):
            with self.subTest(model=model), self.assertRaises(ValueError):
                gateway.resolve_decisions_model(model)

    def test_decisions_payload_forces_model_and_authenticated_user(self):
        payload = {
            "model": "jev-latest",
            "state": "Please call tomorrow.",
            "questions": {
                "callback": {
                    "type": "noul",
                    "instructions": "Was a callback requested?",
                }
            },
            "user": "forged-user",
        }
        upstream = gateway.prepare_decisions_payload(payload, "dcc-user")
        self.assertEqual(upstream["model"], gateway.OPENROUTER_JEV_MODEL)
        self.assertEqual(upstream["user"], "dcc-user:api")
        self.assertEqual(payload["user"], "forged-user")

    def test_decisions_payload_requires_state_and_questions(self):
        invalid = (
            {"model": "jev-latest", "questions": {"x": {"type": "noul"}}},
            {"model": "jev-latest", "state": "x", "questions": {}},
            {"model": "jev-latest", "state": "x", "questions": []},
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                gateway.prepare_decisions_payload(payload, "dcc-user")

    def test_decisions_token_total_uses_provider_reported_usage(self):
        response = {
            "usage": {
                "input_tokens": 312,
                "output_tokens": 48,
                "cost": 0.000013,
            }
        }
        self.assertEqual(gateway.decisions_token_total(response), 360)
        self.assertEqual(gateway.decisions_token_total({}), 0)

    def test_jev_usage_log_contains_metadata_but_not_request_or_answers(self):
        response = {
            "id": "decision_test",
            "model": "typesafe/jev-1.13-20260917",
            "provider": "TypeSafe",
            "answers": {"private": {"type": "noul", "noul": 0.9}},
            "usage": {"input_tokens": 10, "output_tokens": 2, "cost": 0.0001},
        }
        original = gateway.JEV_USAGE_FILE
        with tempfile.TemporaryDirectory() as tempdir:
            gateway.JEV_USAGE_FILE = str(pathlib.Path(tempdir) / "jev.jsonl")
            try:
                self.assertTrue(gateway.append_jev_usage("user-1", response))
                event = json.loads(pathlib.Path(gateway.JEV_USAGE_FILE).read_text())
            finally:
                gateway.JEV_USAGE_FILE = original
        self.assertEqual(event["end_user"], "user-1:api")
        self.assertEqual(event["total_tokens"], 12)
        self.assertNotIn("answers", event)
        self.assertNotIn("state", event)
        self.assertNotIn("questions", event)

    def test_jev_monthly_counter_is_separate_from_regular_api_counter(self):
        regular_original = gateway.TOKEN_USAGE_FILE
        jev_original = gateway.JEV_TOKEN_USAGE_FILE
        with tempfile.TemporaryDirectory() as tempdir:
            gateway.TOKEN_USAGE_FILE = str(pathlib.Path(tempdir) / "regular.json")
            gateway.JEV_TOKEN_USAGE_FILE = str(pathlib.Path(tempdir) / "jev.json")
            try:
                gateway.add_tokens("user-1", 11)
                gateway.add_jev_tokens("user-1", 22)
                self.assertEqual(gateway.month_tokens_used("user-1"), 11)
                self.assertEqual(gateway.jev_month_tokens_used("user-1"), 22)
            finally:
                gateway.TOKEN_USAGE_FILE = regular_original
                gateway.JEV_TOKEN_USAGE_FILE = jev_original
        self.assertEqual(gateway.MONTHLY_TOKEN_LIMIT, 15_000_000)
        self.assertEqual(gateway.JEV_MONTHLY_TOKEN_LIMIT, 50_000_000)

    def test_model_resolution_uses_an_explicit_allowlist(self):
        self.assertEqual(gateway.resolve_model("dccai.dccai-high-vision"), ("dccai-high", False))
        self.assertEqual(gateway.resolve_model("dccai-low"), ("dccai-low", False))
        self.assertEqual(gateway.resolve_model("dccai.dccai-code"), ("dccai-code", True))

    def test_local_and_unknown_models_are_rejected(self):
        for model in (
            "dccai.dccai-local-80b",
            "dccai-local-80b",
            "unknown-model",
            "evil.dccai-low",
            "evil.dccai-code",
        ):
            with self.subTest(model=model), self.assertRaises(ValueError):
                gateway.resolve_model(model)

    def test_nested_function_tool_is_flattened(self):
        payload = {
            "tools": [{"type": "function", "function": copy.deepcopy(FUNCTION)}],
            "tool_choice": {
                "type": "function",
                "function": {"name": "get_weather"},
            },
        }
        gateway.normalize_request_tools(payload)
        self.assertEqual(payload["tools"], [{"type": "function", **FUNCTION}])
        self.assertEqual(payload["tool_choice"], "auto")
        self.assertIn("get_weather", payload["instructions"])

    def test_fullwidth_dsml_becomes_function_call(self):
        response = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": (
                                "<｜DSML｜tool_calls>\n"
                                "<｜DSML｜invoke name=\"get_weather\">\n"
                                "<｜DSML｜parameter name=\"city\" string=\"true\">Tokyo</｜DSML｜parameter>\n"
                                "<｜DSML｜parameter name=\"days\" string=\"false\">3</｜DSML｜parameter>\n"
                                "</｜DSML｜invoke>\n"
                                "</｜DSML｜tool_calls>"
                            ),
                        }
                    ],
                }
            ]
        }
        payload = {"tools": [{"type": "function", **FUNCTION}]}
        self.assertTrue(gateway.normalize_dsml_response(response, payload))
        call = response["output"][1]
        self.assertEqual(call["type"], "function_call")
        self.assertEqual(call["name"], "get_weather")
        self.assertEqual(json.loads(call["arguments"]), {"city": "Tokyo", "days": 3})
        self.assertEqual(response["output"][0]["content"][0]["text"], "")

    def test_ascii_dsml_becomes_custom_tool_call(self):
        response = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": (
                                "<|DSML|tool_calls>\n"
                                "<|DSML|invoke name=\"shell\">\n"
                                "<|DSML|parameter name=\"content\" string=\"true\">pwd</|DSML|parameter>\n"
                                "</|DSML|invoke>\n"
                                "</|DSML|tool_calls>"
                            ),
                        }
                    ],
                }
            ]
        }
        payload = {"tools": [{"type": "custom", "name": "shell"}]}
        self.assertTrue(gateway.normalize_dsml_response(response, payload))
        call = response["output"][1]
        self.assertEqual(call["type"], "custom_tool_call")
        self.assertEqual(call["name"], "shell")
        self.assertEqual(call["input"], "pwd")

    def test_unknown_dsml_tool_is_left_as_text(self):
        text = (
            "<|DSML|tool_calls>\n"
            "<|DSML|invoke name=\"not_offered\">\n"
            "</|DSML|invoke>\n"
            "</|DSML|tool_calls>"
        )
        response = {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": text}]}
            ]
        }
        payload = {"tools": [{"type": "custom", "name": "shell"}]}
        self.assertFalse(gateway.normalize_dsml_response(response, payload))
        self.assertEqual(response["output"][0]["content"][0]["text"], text)

    def test_custom_sse_uses_custom_event_names(self):
        response = {
            "id": "resp_test",
            "status": "completed",
            "output": [
                {
                    "type": "custom_tool_call",
                    "id": "call_test",
                    "call_id": "call_test",
                    "name": "shell",
                    "input": "pwd",
                    "status": "completed",
                }
            ],
        }
        event_types = [e["type"] for e in gateway.response_sse_events(response)]
        self.assertIn("response.custom_tool_call_input.delta", event_types)
        self.assertIn("response.custom_tool_call_input.done", event_types)
        self.assertNotIn("response.function_call_arguments.delta", event_types)

    def test_buffered_message_keeps_output_text_events(self):
        response = {
            "id": "resp_test",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "id": "msg_test",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": "Checking now.", "annotations": []}
                    ],
                }
            ],
        }
        events = list(gateway.response_sse_events(response))
        deltas = [e for e in events if e["type"] == "response.output_text.delta"]
        self.assertEqual([e["delta"] for e in deltas], ["Checking now."])

    def test_reasoning_items_are_removed_but_answer_and_tool_calls_remain(self):
        response = {
            "output": [
                {
                    "type": "reasoning",
                    "id": "reasoning_1",
                    "content": [{"type": "reasoning_text", "text": "private chain"}],
                },
                {
                    "type": "message",
                    "id": "message_1",
                    "content": [{"type": "output_text", "text": "Final answer"}],
                },
                {
                    "type": "function_call",
                    "id": "call_1",
                    "name": "get_weather",
                    "arguments": "{}",
                },
            ]
        }
        self.assertEqual(gateway.sanitize_reasoning_output(response), 1)
        self.assertEqual(
            [item["type"] for item in response["output"]],
            ["message", "function_call"],
        )
        self.assertEqual(response["output"][0]["content"][0]["text"], "Final answer")


if __name__ == "__main__":
    unittest.main()
