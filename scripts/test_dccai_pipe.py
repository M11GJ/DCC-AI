#!/usr/bin/env python3
"""Unit tests for DCC AI Pipe model exposure and Local 80B policy."""

import asyncio
import json
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import dccai_pipe
from dccai_pipe import Pipe


class FakeStreamingResponse:
    status_code = 200

    async def aiter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"TEST"}}]}'
        await asyncio.Event().wait()


class FakeStreamContext:
    async def __aenter__(self):
        return FakeStreamingResponse()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def stream(self, *args, **kwargs):
        return FakeStreamContext()


class DccAiPipeTests(unittest.TestCase):
    def setUp(self):
        dccai_pipe._STATE_BY_GROUP.clear()
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

    def test_dsml_tool_call_is_normalized_for_chat_completions(self):
        response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": (
                            '<｜DSML｜tool_calls><｜DSML｜invoke name="query_knowledge_bases">'
                            '<｜DSML｜parameter name="query" string="true">DCC Discord 活動'
                            '</｜DSML｜parameter></｜DSML｜invoke></｜DSML｜tool_calls>'
                        ),
                    },
                    "finish_reason": "stop",
                }
            ]
        }
        payload = {
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "query_knowledge_bases", "parameters": {}},
                }
            ]
        }

        self.assertTrue(dccai_pipe._normalize_chat_dsml_response(response, payload))
        choice = response["choices"][0]
        self.assertEqual(choice["finish_reason"], "tool_calls")
        self.assertIsNone(choice["message"]["content"])
        call = choice["message"]["tool_calls"][0]
        self.assertEqual(call["function"]["name"], "query_knowledge_bases")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"query": "DCC Discord 活動"})

    def test_double_bar_dsml_tool_call_is_normalized(self):
        response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": (
                            '<｜｜DSML｜｜tool_calls>\n'
                            '<｜｜DSML｜｜invoke name="search_knowledge_bases">\n'
                            '<｜｜DSML｜｜parameter name="query" string="true">DCC Discord 活動'
                            '</｜｜DSML｜｜parameter>\n'
                            '</｜｜DSML｜｜invoke>\n'
                            '</｜｜DSML｜｜tool_calls>'
                        ),
                    },
                    "finish_reason": "stop",
                }
            ]
        }
        payload = {
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "search_knowledge_bases"},
                }
            ]
        }

        self.assertTrue(dccai_pipe._normalize_chat_dsml_response(response, payload))
        call = response["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(call["function"]["name"], "search_knowledge_bases")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"query": "DCC Discord 活動"})

    def test_dsml_tool_name_resolves_to_open_webui_prefix(self):
        response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": (
                            '<｜｜DSML｜｜tool_calls>'
                            '<｜｜DSML｜｜invoke name="search_knowledge_bases">'
                            '<｜｜DSML｜｜parameter name="query" string="true">DCC'
                            '</｜｜DSML｜｜parameter>'
                            '</｜｜DSML｜｜invoke>'
                            '</｜｜DSML｜｜tool_calls>'
                        ),
                    }
                }
            ]
        }
        payload = {
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "builtin__search_knowledge_bases"},
                }
            ]
        }

        self.assertTrue(dccai_pipe._normalize_chat_dsml_response(response, payload))
        call = response["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(call["function"]["name"], "builtin__search_knowledge_bases")

    def test_known_builtin_dsml_tool_is_allowed_when_native_tools_are_omitted(self):
        response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": (
                            '<｜｜DSML｜｜tool_calls>'
                            '<｜｜DSML｜｜invoke name="query_knowledge_bases">'
                            '<｜｜DSML｜｜parameter name="query" string="true">DCC'
                            '</｜｜DSML｜｜parameter></｜｜DSML｜｜invoke>'
                            '</｜｜DSML｜｜tool_calls>'
                        ),
                    }
                }
            ]
        }

        self.assertTrue(dccai_pipe._normalize_chat_dsml_response(response, {}))
        call = response["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(call["function"]["name"], "query_knowledge_bases")

    def test_unknown_dsml_tool_is_rejected_when_native_tools_are_omitted(self):
        response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": (
                            '<｜DSML｜tool_calls><｜DSML｜invoke name="delete_everything">'
                            '</｜DSML｜invoke></｜DSML｜tool_calls>'
                        ),
                    }
                }
            ]
        }

        self.assertFalse(dccai_pipe._normalize_chat_dsml_response(response, {}))
        self.assertNotIn("tool_calls", response["choices"][0]["message"])

    def test_self_closing_knowledge_call_uses_last_user_text_as_query(self):
        response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": (
                            '<｜｜DSML｜｜tool_calls>\n'
                            '<｜｜DSML｜｜invoke name="search_knowledge_bases" />\n'
                            '</｜｜DSML｜｜tool_calls>'
                        ),
                    }
                }
            ]
        }
        payload = {
            "messages": [
                {"role": "system", "content": "routing"},
                {"role": "user", "content": "DCC Discordの活動を教えて"},
            ]
        }

        self.assertTrue(dccai_pipe._normalize_chat_dsml_response(response, payload))
        call = response["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(
            json.loads(call["function"]["arguments"]),
            {"query": "DCC Discordの活動を教えて"},
        )

    def test_stream_prefix_detects_dsml_without_delaying_plain_text(self):
        self.assertEqual(dccai_pipe._dsml_prefix_state("<｜DS"), "prefix")
        self.assertEqual(
            dccai_pipe._dsml_prefix_state("<｜DSML｜tool_calls>"), "dsml"
        )
        self.assertEqual(dccai_pipe._dsml_prefix_state("<｜｜DS"), "prefix")
        self.assertEqual(
            dccai_pipe._dsml_prefix_state("<｜｜DSML｜｜tool_calls>"), "dsml"
        )
        self.assertEqual(dccai_pipe._dsml_prefix_state("通常の回答です"), "plain")

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

    def test_unconsumed_stream_does_not_take_a_slot(self):
        async def scenario():
            stream = await self.pipe.pipe(
                {
                    "model": "dccai.dccai-local-80b",
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                },
                __user__={"id": "test-user"},
                __metadata__={"chat_id": "chat-test"},
            )
            state = dccai_pipe._STATE_BY_GROUP["local"]
            self.assertEqual(state["active"], 0)
            self.assertEqual(state["waiting"], 0)
            self.assertEqual(state["sem"]._value, 1)
            await stream.aclose()

        asyncio.run(scenario())

    def test_stopped_stream_releases_slot_and_next_stream_runs(self):
        async def wrapped(stream):
            try:
                async for line in stream:
                    yield line
            finally:
                await stream.aclose()

        async def new_stream(chat_id):
            return await self.pipe.pipe(
                {
                    "model": "dccai.dccai-local-80b",
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                },
                __user__={"id": "test-user"},
                __metadata__={"chat_id": chat_id},
            )

        async def scenario():
            first_inner = await new_stream("chat-first")
            first_outer = wrapped(first_inner)
            self.assertIn("TEST", await asyncio.wait_for(anext(first_outer), 1))
            state = dccai_pipe._STATE_BY_GROUP["local"]
            self.assertEqual(state["active"], 1)

            # Open WebUI closes its outer response iterator when the user presses stop.
            await first_outer.aclose()
            await asyncio.sleep(0)
            self.assertEqual(state["active"], 0)
            self.assertEqual(state["sem"]._value, 1)

            second = await new_stream("chat-second")
            self.assertIn("TEST", await asyncio.wait_for(anext(second), 1))
            await second.aclose()
            self.assertEqual(state["active"], 0)
            self.assertEqual(state["waiting"], 0)
            self.assertEqual(state["sem"]._value, 1)

        with mock.patch.object(dccai_pipe.httpx, "AsyncClient", FakeAsyncClient):
            asyncio.run(scenario())

    def test_stream_emits_thinking_status_before_first_content(self):
        async def scenario():
            events = []

            async def emitter(event):
                events.append(event)

            stream = await self.pipe.pipe(
                {
                    "model": "dccai.dccai-low-vision",
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                },
                __user__={"id": "test-user"},
                __metadata__={"chat_id": "chat-thinking-status"},
                __event_emitter__=emitter,
            )
            self.assertIn("TEST", await asyncio.wait_for(anext(stream), 1))
            self.assertGreaterEqual(len(events), 2)
            self.assertEqual(events[0]["type"], "status")
            self.assertEqual(events[0]["data"]["description"], "🧠 考えています…")
            self.assertFalse(events[0]["data"]["done"])
            await stream.aclose()

        with mock.patch.object(dccai_pipe.httpx, "AsyncClient", FakeAsyncClient):
            asyncio.run(scenario())

    def test_cancelled_waiter_does_not_corrupt_queue(self):
        async def new_stream(chat_id):
            return await self.pipe.pipe(
                {
                    "model": "dccai.dccai-local-80b",
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                },
                __user__={"id": "test-user"},
                __metadata__={"chat_id": chat_id},
            )

        async def scenario():
            first = await new_stream("chat-first")
            await asyncio.wait_for(anext(first), 1)
            state = dccai_pipe._STATE_BY_GROUP["local"]

            waiting_stream = await new_stream("chat-waiting")
            waiting_task = asyncio.create_task(anext(waiting_stream))
            await asyncio.sleep(0)
            self.assertEqual(state["active"], 1)
            self.assertEqual(state["waiting"], 1)

            waiting_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiting_task
            self.assertEqual(state["active"], 1)
            self.assertEqual(state["waiting"], 0)

            await first.aclose()
            self.assertEqual(state["active"], 0)
            self.assertEqual(state["sem"]._value, 1)

        with mock.patch.object(dccai_pipe.httpx, "AsyncClient", FakeAsyncClient):
            asyncio.run(scenario())

    def test_queue_wait_timeout_does_not_leave_a_waiter(self):
        self.pipe.valves.MAX_QUEUE_WAIT = 0

        async def new_stream(chat_id):
            return await self.pipe.pipe(
                {
                    "model": "dccai.dccai-local-80b",
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                },
                __user__={"id": "test-user"},
                __metadata__={"chat_id": chat_id},
            )

        async def scenario():
            first = await new_stream("chat-first")
            await asyncio.wait_for(anext(first), 1)
            state = dccai_pipe._STATE_BY_GROUP["local"]

            waiting_stream = await new_stream("chat-timeout")
            message = await asyncio.wait_for(anext(waiting_stream), 1)
            self.assertIn("混み合っています", message)
            self.assertEqual(state["active"], 1)
            self.assertEqual(state["waiting"], 0)

            await waiting_stream.aclose()
            await first.aclose()
            self.assertEqual(state["active"], 0)
            self.assertEqual(state["sem"]._value, 1)

        with mock.patch.object(dccai_pipe.httpx, "AsyncClient", FakeAsyncClient):
            asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
