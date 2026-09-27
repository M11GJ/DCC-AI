#!/usr/bin/env python3
"""Apply scoped public-error formatting to Open WebUI v0.11.3 exception boundaries."""
from pathlib import Path
import sys


def patch(root):
    root=Path(root)
    changes={
        'tools/builtin.py': [("{'error': str(e)}", "{'error': dcc_public_error(e, source='webui_tool')['message']}")],
        'utils/middleware.py': [
            ("{'error': str(e)}", "{'error': dcc_public_error(e, source='webui_tool')['message']}"),
            ('error_content = get_message_error_content(e)', "error_content = dcc_public_error(e, source='webui_completion')['message']"),
        ],
        'main.py': [("error_detail = e.detail if isinstance(e, HTTPException) else str(e)", "error_detail = dcc_public_error(e, getattr(e, 'status_code', None), 'webui_request')['message']")],
    }
    for name,replacements in changes.items():
        path=root/name;s=path.read_text()
        if 'from dccai_errors import public_error as dcc_public_error' in s:
            continue
        for old,new in replacements:
            if old not in s:raise RuntimeError(f'Expected error boundary missing: {name}: {old}')
            s=s.replace(old,new)
        import_line='from dccai_errors import public_error as dcc_public_error\n'
        future='from __future__ import annotations\n'
        if future in s:s=s.replace(future,future+import_line,1)
        else:s=import_line+s
        compile(s,str(path),'exec')
        path.write_text(s)
        print('Patched',name)
if __name__=='__main__':patch(sys.argv[1])
