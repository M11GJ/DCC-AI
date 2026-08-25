#!/usr/bin/env python3

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from patch_open_webui_stream_close import (
    ASYNC_GENERATOR_AFTER,
    ASYNC_GENERATOR_BEFORE,
    STREAMING_RESPONSE_AFTER,
    STREAMING_RESPONSE_BEFORE,
    patch_source,
)


class OpenWebUiStreamPatchTests(unittest.TestCase):
    def test_patch_closes_both_nested_stream_types(self):
        source = STREAMING_RESPONSE_BEFORE + "\n" + ASYNC_GENERATOR_BEFORE
        patched = patch_source(source)
        self.assertIn(STREAMING_RESPONSE_AFTER, patched)
        self.assertIn(ASYNC_GENERATOR_AFTER, patched)
        self.assertEqual(patched.count("aclose()"), 2)

    def test_patch_is_idempotent(self):
        source = STREAMING_RESPONSE_BEFORE + "\n" + ASYNC_GENERATOR_BEFORE
        once = patch_source(source)
        self.assertEqual(patch_source(once), once)


if __name__ == "__main__":
    unittest.main()
