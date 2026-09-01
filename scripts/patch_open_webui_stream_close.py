#!/usr/bin/env python3
"""Make Open WebUI close nested Pipe streams when a chat is stopped."""

from pathlib import Path
import sys


STREAMING_RESPONSE_BEFORE = """                if isinstance(res, StreamingResponse):
                    async for data in res.body_iterator:
                        yield data
                    return
"""

STREAMING_RESPONSE_AFTER = """                if isinstance(res, StreamingResponse):
                    try:
                        async for data in res.body_iterator:
                            yield data
                    finally:
                        if hasattr(res.body_iterator, 'aclose'):
                            await res.body_iterator.aclose()
                    return
"""

ASYNC_GENERATOR_BEFORE = """            if isinstance(res, AsyncGenerator):
                async for line in res:
                    yield process_line(form_data, line)
"""

ASYNC_GENERATOR_AFTER = """            if isinstance(res, AsyncGenerator):
                try:
                    async for line in res:
                        yield process_line(form_data, line)
                finally:
                    await res.aclose()
"""


def patch_source(source: str) -> str:
    replacements = (
        (STREAMING_RESPONSE_BEFORE, STREAMING_RESPONSE_AFTER),
        (ASYNC_GENERATOR_BEFORE, ASYNC_GENERATOR_AFTER),
    )
    for before, after in replacements:
        if after in source:
            continue
        if source.count(before) != 1:
            raise RuntimeError("Open WebUI stream wrapper did not match the expected v0.11.3 source")
        source = source.replace(before, after, 1)
    return source


def main(path: str) -> None:
    target = Path(path)
    original = target.read_text(encoding="utf-8")
    patched = patch_source(original)
    target.write_text(patched, encoding="utf-8")
    print(f"Patched nested stream cleanup in {target}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} OPEN_WEBUI_FUNCTIONS_PY")
    main(sys.argv[1])
