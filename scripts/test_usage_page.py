#!/usr/bin/env python3

import hashlib
import http.cookies
import pathlib
import sys
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


if __name__ == "__main__":
    unittest.main()
