import sqlite3, glob, json

db_path = glob.glob("/app/backend/data/webui.db")[0]
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

# 新しいナレッジID
new_knowledge_id = "4b147a5c-7d05-41f6-ae8b-b71739dfac1b"

# 対象のモデルを取得
models = conn.execute("select id, name, meta from model where id in ('dccai.dccai-high-vision', 'dccai.dccai-low-vision')").fetchall()

for m in models:
    model_id = m['id']
    meta_str = m['meta']
    
    print(f"Model: {model_id}")
    try:
        meta = json.loads(meta_str) if meta_str else {}
    except Exception as e:
        print(f"  Error parsing meta: {e}")
        meta = {}
    
    # 新しい紐付けデータを作成
    meta['knowledge'] = [
        {
            "id": new_knowledge_id,
            "name": "DCC Discord",
            "type": "collection"
        }
    ]
    
    new_meta_str = json.dumps(meta, ensure_ascii=False)
    conn.execute("update model set meta = ? where id = ?", (new_meta_str, model_id))
    print(f"  Updated model meta knowledge successfully")

conn.commit()
conn.close()
print("Model knowledge mapping updated successfully")
