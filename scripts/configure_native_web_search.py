#!/usr/bin/env python3
"""Enable iterative web tools for DCC cloud models, preserving other settings."""
import json
import sqlite3
import time
from pathlib import Path

MODEL_IDS = ('dccai.dccai-high-vision', 'dccai.dccai-low-vision', 'dccai.dccai-code')

def configure(conn):
    rows = []
    for model_id in MODEL_IDS:
        row = conn.execute('SELECT id, params, meta FROM model WHERE id=?', (model_id,)).fetchone()
        if row is None:
            raise RuntimeError(f'Missing model: {model_id}')
        rows.append(row)
    backup = Path('/app/backend/data') / f'dccai-native-search-before-{time.time_ns()}.json'
    backup.write_text(json.dumps(rows, ensure_ascii=False))
    backup.chmod(0o600)
    with conn:
        for model_id, raw_params, raw_meta in rows:
            params = json.loads(raw_params or '{}')
            meta = json.loads(raw_meta or '{}')
            params['function_calling'] = 'native'
            meta.setdefault('capabilities', {}).update(web_search=True, builtin_tools=True)
            meta.setdefault('builtinTools', {})['web_search'] = True
            defaults = meta.setdefault('defaultFeatureIds', [])
            if 'web_search' not in defaults:
                defaults.append('web_search')
            conn.execute('UPDATE model SET params=?,meta=?,updated_at=? WHERE id=?',
                         (json.dumps(params,ensure_ascii=False),json.dumps(meta,ensure_ascii=False),int(time.time()),model_id))
    print('Backup:', backup)
    for model_id in MODEL_IDS:
        print(model_id, 'native web search enabled')

if __name__ == '__main__':
    with sqlite3.connect('/app/backend/data/webui.db') as conn:
        configure(conn)
