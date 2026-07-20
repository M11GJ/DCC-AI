#!/usr/bin/env python3
"""Geminiキーの生死を判定し、生きているキーだけを LiteLLM 構成に反映する。
死んだキーは自動的に外れ、復活したら自動的に戻る。変更があった時だけ litellm を再起動する。"""
import json, os, re, subprocess, sys, urllib.request, urllib.error
from datetime import datetime

ENV='/opt/dccai/.env'
CFG='/opt/dccai/litellm/config.yaml'
LOG='/opt/dccai/scripts/gemini-key-health.log'
MODEL='gemini-3.1-flash-lite'
START_MARK='GEMINI_LOW_START'
END_MARK='GEMINI_LOW_END'

def log(m):
    line=f"[{datetime.now().isoformat(timespec='seconds')}] {m}"
    print(line)
    with open(LOG,'a',encoding='utf-8') as f: f.write(line+'\n')

def load_keys():
    keys={}
    for l in open(ENV,encoding='utf-8'):
        m=re.match(r'^(GEMINI_API_KEY_\d+)=(.+)$', l.strip())
        if m: keys[m.group(1)]=m.group(2)
    return dict(sorted(keys.items(), key=lambda kv: int(kv[0].rsplit('_',1)[1])))

def alive(key):
    url=f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={key}"
    data=json.dumps({"contents":[{"parts":[{"text":"hi"}]}]}).encode()
    req=urllib.request.Request(url, data=data, headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status==200
    except urllib.error.HTTPError as e:
        return False
    except Exception:
        return None  # ネットワーク不調などは判定不能=現状維持

def block(name):
    return ("  - model_name: dccai-low\n"
            "    litellm_params:\n"
            f"      model: gemini/{MODEL}\n"
            f"      api_key: os.environ/{name}\n"
            "      rpm: 60\n"
            "    model_info: { input_cost_per_token: 0, output_cost_per_token: 0 }")

def main():
    keys=load_keys()
    if not keys: log("キーが見つかりません"); return 1
    healthy=[]; unknown=[]
    for n,k in keys.items():
        a=alive(k)
        if a is True: healthy.append(n)
        elif a is None: unknown.append(n)
    txt=open(CFG,encoding='utf-8').read()
    lines=txt.split('\n')
    si=next((i for i,l in enumerate(lines) if START_MARK in l), None)
    ei=next((i for i,l in enumerate(lines) if END_MARK in l), None)
    if si is None or ei is None: log("マーカーが無いので中断"); return 1
    current=sorted(set(re.findall(r'GEMINI_API_KEY_\d+', '\n'.join(lines[si:ei+1]))))
    # 判定不能なキーは現状維持（誤って外さない）
    keep=sorted(set(healthy) | (set(unknown) & set(current)),
                key=lambda n: int(n.rsplit('_',1)[1]))
    if not keep:
        log(f"生存キーが0本。安全のため構成を変更しません（現状: {current}）"); return 0
    if keep==current:
        log(f"変更なし。稼働中: {keep} / 除外中: {[k for k in keys if k not in keep]}"); return 0
    new=lines[:si+1]+[block(n) for n in keep]+lines[ei:]
    open(CFG,'w',encoding='utf-8').write('\n'.join(new))
    log(f"構成を更新: {current} -> {keep}")
    r=subprocess.run(['docker','compose','restart','litellm'], cwd='/opt/dccai',
                     capture_output=True, text=True)
    log("litellm 再起動 " + ("成功" if r.returncode==0 else f"失敗: {r.stderr[:200]}"))
    return 0

if __name__=='__main__':
    sys.exit(main())
