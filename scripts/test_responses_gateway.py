import copy
import json
import unittest

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
