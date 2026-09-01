#!/usr/bin/env python3

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from patch_open_webui_auth_cache import (
    AUTH_REDIRECT_AFTER,
    AUTH_REDIRECT_BEFORE,
    ERROR_REDIRECT_AFTER,
    ERROR_REDIRECT_BEFORE,
    patch_source,
)


class OpenWebUiAuthCachePatchTests(unittest.TestCase):
    def test_patch_adds_release_query_and_preserves_error_query(self):
        source = AUTH_REDIRECT_BEFORE + ERROR_REDIRECT_BEFORE
        patched = patch_source(source)
        self.assertIn(AUTH_REDIRECT_AFTER, patched)
        self.assertIn(ERROR_REDIRECT_AFTER, patched)

    def test_patch_is_idempotent(self):
        source = AUTH_REDIRECT_BEFORE + ERROR_REDIRECT_BEFORE
        once = patch_source(source)
        self.assertEqual(patch_source(once), once)


if __name__ == "__main__":
    unittest.main()
