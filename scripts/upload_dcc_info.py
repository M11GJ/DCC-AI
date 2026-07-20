import sqlite3, glob, io, json, sys, httpx
from open_webui.utils.auth import create_token

db_path = glob.glob("/app/backend/data/webui.db")[0]
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
admin = conn.execute("SELECT id FROM user WHERE role='admin' LIMIT 1").fetchone()
tok = create_token(data={'id': admin['id']})
headers = {'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'}

COLLECTION_NAME = "DCC Discord"
OWUI_BASE = "http://localhost:8080"

# 1. コレクションIDを取得
r = httpx.get(f"{OWUI_BASE}/api/v1/knowledge/", headers=headers, timeout=15)
col_id = None
for item in r.json().get('items', []):
    if item.get('name') == COLLECTION_NAME:
        col_id = item['id']
        break

if not col_id:
    print("Error: DCC Discord collection not found")
    sys.exit(1)

print(f"Collection ID: {col_id}")

# 2. ファイルを読み込む
try:
    with open('/tmp/dcc_info.txt', 'r', encoding='utf-8') as f:
        content = f.read()
except Exception as e:
    print(f"Error reading file: {e}")
    sys.exit(1)

# 3. ファイルをアップロード
print("Uploading file to Open WebUI...")
files = {"file": ("dcc_member_info_and_guidelines.txt", io.BytesIO(content.encode('utf-8')), "text/plain")}
r_upload = httpx.post(f"{OWUI_BASE}/api/v1/files/",
                      headers={"Authorization": f"Bearer {tok}"},
                      files=files, timeout=60)

if r_upload.status_code != 200:
    print(f"Failed to upload: {r_upload.status_code} {r_upload.text}")
    sys.exit(1)

file_id = r_upload.json()["id"]
print(f"File uploaded. File ID: {file_id}")

# 4. コレクションに追加（これで自動的にベクトル化される）
print("Adding file to collection...")
r_add = httpx.post(f"{OWUI_BASE}/api/v1/knowledge/{col_id}/file/add",
                   json={"file_id": file_id},
                   headers=headers,
                   timeout=120)

if r_add.status_code == 200:
    print("SUCCESS: DCC member info and guidelines added to knowledge base successfully")
else:
    print(f"Failed to add to collection: {r_add.status_code} {r_add.text}")
    sys.exit(1)
