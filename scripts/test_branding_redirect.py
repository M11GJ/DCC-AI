import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "branding" / "login_notice.html").read_text(encoding="utf-8")


class BrandingRedirectTests(unittest.TestCase):
    def test_standalone_ai_ui_redirects_to_the_portal(self):
        self.assertIn("window.top === window.self", SOURCE)
        self.assertIn("window.location.hostname === 'ai.shu-dcc.net'", SOURCE)
        self.assertIn(
            "window.location.replace('https://app.shu-dcc.net/ai')",
            SOURCE,
        )

    def test_redirect_guard_runs_before_the_open_webui_ui_mounts(self):
        redirect = SOURCE.index("window.top === window.self")
        dom_ready = SOURCE.index("document.addEventListener('DOMContentLoaded'")
        self.assertLess(redirect, dom_ready)


if __name__ == "__main__":
    unittest.main()
