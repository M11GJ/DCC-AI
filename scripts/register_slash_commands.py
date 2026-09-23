import json,sqlite3,httpx,sys
from pathlib import Path
from open_webui.utils.auth import create_token
c=sqlite3.connect('file:/app/backend/data/webui.db?mode=ro',uri=True)
uid=c.execute("select id from user where role='admin' order by created_at limit 1").fetchone()[0]
commands=json.loads(Path(sys.argv[1]).read_text())
with httpx.Client(base_url='http://127.0.0.1:8080',headers={'Authorization':'Bearer '+create_token({'id':uid})},timeout=30) as client:
 r=client.get('/api/v1/prompts/');r.raise_for_status();existing={p['command']:p for p in r.json()}
 for form in commands:
  old=existing.get(form['command'])
  if old and not (old.get('meta') or {}).get('dcc_command'):raise RuntimeError('Existing user command collision: '+form['command'])
  form['access_grants']=[{'principal_type':'user','principal_id':'*','permission':'read'}]
  route='/api/v1/prompts/id/'+old['id']+'/update' if old else '/api/v1/prompts/create'
  r=client.post(route,json=form);r.raise_for_status();print(form['command'],'registered')
 # Verify visibility with an existing regular user's permissions.
 user=c.execute("select id from user where role='user' limit 1").fetchone()
 if user:
  r=client.get('/api/v1/prompts/',headers={'Authorization':'Bearer '+create_token({'id':user[0]})});r.raise_for_status()
  visible={p['command'] for p in r.json()};assert all(p['command'] in visible for p in commands)
  print('REGULAR_USER_COMMANDS_OK')
