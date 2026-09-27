#!/usr/bin/env python3
"""Give the OAuth landing URL a release query so stale HTML is bypassed."""

from pathlib import Path
import sys


AUTH_REDIRECT_BEFORE = "        redirect_url = f'{redirect_base_url}/auth'\n"
AUTH_REDIRECT_AFTER = "        redirect_url = f'{redirect_base_url}/auth?v=0.11.4'\n"
ERROR_REDIRECT_BEFORE = (
    "            redirect_url = f'{redirect_url}?error={urllib.parse.quote_plus(error_message)}'\n"
)
ERROR_REDIRECT_AFTER = (
    "            redirect_url = f'{redirect_url}&error={urllib.parse.quote_plus(error_message)}'\n"
)


def patch_source(source: str) -> str:
    replacements = (
        (AUTH_REDIRECT_BEFORE, AUTH_REDIRECT_AFTER),
        (ERROR_REDIRECT_BEFORE, ERROR_REDIRECT_AFTER),
    )
    for before, after in replacements:
        if after in source:
            continue
        if source.count(before) != 1:
            raise RuntimeError("Open WebUI OAuth redirect did not match the expected v0.11.4 source")
        source = source.replace(before, after, 1)
    return source


def main(path: str) -> None:
    target = Path(path)
    original = target.read_text(encoding="utf-8")
    patched = patch_source(original)
    target.write_text(patched, encoding="utf-8")
    print(f"Patched OAuth landing cache key in {target}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} OPEN_WEBUI_OAUTH_PY")
    main(sys.argv[1])
