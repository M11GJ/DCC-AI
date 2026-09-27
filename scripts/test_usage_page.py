#!/usr/bin/env python3

import hashlib
import http.cookies
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import usage_page


class UsagePageAuthTests(unittest.TestCase):
    def setUp(self):
        self.original_secret = usage_page.SESSION_SECRET
        usage_page.SESSION_SECRET = "test-session-secret"

    def tearDown(self):
        usage_page.SESSION_SECRET = self.original_secret

    def test_login_page_offers_both_authentication_methods(self):
        page = usage_page.login_page()
        self.assertIn('href="/login/dcc"', page)
        self.assertIn('href="/login/discord"', page)

    def test_oauth_flow_round_trip_and_tamper_rejection(self):
        with mock.patch.object(usage_page.time, "time", return_value=1_000):
            signed = usage_page.make_oauth_flow("state-value", "verifier-value")
        header = f"{usage_page.DCC_FLOW_COOKIE_NAME}={signed}"
        with mock.patch.object(usage_page.time, "time", return_value=1_001):
            self.assertEqual(
                usage_page.read_oauth_flow(header, usage_page.DCC_FLOW_COOKIE_NAME),
                ("state-value", "verifier-value"),
            )
        tampered = signed[:-1] + ("0" if signed[-1] != "0" else "1")
        self.assertIsNone(
            usage_page.read_oauth_flow(
                f"{usage_page.DCC_FLOW_COOKIE_NAME}={tampered}",
                usage_page.DCC_FLOW_COOKIE_NAME,
            )
        )

    def test_oauth_flow_expires(self):
        with mock.patch.object(usage_page.time, "time", return_value=1_000):
            signed = usage_page.make_oauth_flow("state-value")
        with mock.patch.object(usage_page.time, "time", return_value=1_000 + usage_page.OAUTH_FLOW_TTL + 1):
            self.assertIsNone(
                usage_page.read_oauth_flow(
                    f"{usage_page.DISCORD_FLOW_COOKIE_NAME}={signed}",
                    usage_page.DISCORD_FLOW_COOKIE_NAME,
                )
            )

    def test_pkce_pair_is_s256_and_verifier_has_valid_length(self):
        verifier, challenge = usage_page.make_pkce_pair()
        self.assertGreaterEqual(len(verifier), 43)
        self.assertLessEqual(len(verifier), 128)
        expected = usage_page.base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        self.assertEqual(challenge, expected)

    def test_dcc_identity_requires_member_and_valid_discord_id(self):
        self.assertEqual(
            usage_page.dcc_discord_id({
                "dcc_member": True,
                "discord_id": "123456789012345678",
                "discord_ids": ["223456789012345678", "123456789012345678"],
            }),
            "123456789012345678",
        )
        self.assertEqual(
            usage_page.dcc_discord_ids({
                "dcc_member": True,
                "discord_id": "123456789012345678",
                "discord_ids": ["223456789012345678", "123456789012345678"],
            }),
            ["123456789012345678", "223456789012345678"],
        )
        self.assertIsNone(usage_page.dcc_discord_id({"dcc_member": False, "discord_id": "123456789012345678"}))
        self.assertIsNone(usage_page.dcc_discord_id({"dcc_member": True, "discord_id": "not-an-id"}))
        self.assertIsNone(usage_page.dcc_discord_id({"dcc_member": True}))

    def test_auth_cookies_are_http_only_secure_and_lax(self):
        cookie = usage_page.session_cookie("123456789012345678")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=Lax", cookie)
        parsed = http.cookies.SimpleCookie()
        parsed.load(cookie)
        self.assertIn(usage_page.COOKIE_NAME, parsed)

    def test_jev_usage_is_included_in_user_and_admin_model_totals(self):
        event = {
            "timestamp": "2026-09-18T06:27:34+00:00",
            "event_id": "jev-test",
            "end_user": "user-1:api",
            "model_group": "jev-latest",
            "model": "typesafe/jev-1.13-20260917",
            "total_tokens": 439,
        }
        original_file = usage_page.JEV_USAGE_FILE
        original_cache = dict(usage_page._SPEND_LOGS_CACHE)
        with tempfile.TemporaryDirectory() as tempdir:
            path = pathlib.Path(tempdir) / "jev.jsonl"
            path.write_text(json.dumps(event) + "\n", encoding="utf-8")
            usage_page.JEV_USAGE_FILE = str(path)
            usage_page._SPEND_LOGS_CACHE = {"logs": None, "ts": 0.0}
            try:
                with mock.patch.object(usage_page, "_pg_query", return_value=[]):
                    total, by_model = usage_page.litellm_total_and_by_model("user-1:api")
                    admin = usage_page.litellm_all_users_by_model()
            finally:
                usage_page.JEV_USAGE_FILE = original_file
                usage_page._SPEND_LOGS_CACHE = original_cache
        self.assertEqual(total, 439)
        self.assertEqual(by_model, {"jev-latest": 439})
        self.assertEqual(admin["user-1"]["Jev"], 439)

    def test_regular_and_jev_monthly_allowances_are_displayed_separately(self):
        self.assertEqual(usage_page.MONTHLY_TOKEN_LIMIT, 15_000_000)
        self.assertEqual(usage_page.JEV_MONTHLY_TOKEN_LIMIT, 50_000_000)
        self.assertIn("今月の通常API利用状況", usage_page.PAGE_TEMPLATE)
        self.assertIn("今月のJev API利用状況", usage_page.PAGE_TEMPLATE)
        self.assertIn("monthly_jev_rows", usage_page.ADMIN_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
