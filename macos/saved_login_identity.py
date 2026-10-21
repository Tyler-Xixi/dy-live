"""Optional display metadata, separate from the existing account registry."""
import json
import os
import re
import tempfile
from pathlib import Path


def load_identities(data_dir):
    path=Path(data_dir)/'login-user-identities.json'
    if not path.exists(): return {}
    values=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(values,dict): raise ValueError('账号显示资料格式错误')
    for key,value in values.items():
        if not re.fullmatch(r'legacy|[0-9a-f]{32}',key) or not isinstance(value,dict) or set(value)!={'nickname','douyin_id'}:
            raise ValueError('账号显示资料格式错误')
        if any(not isinstance(text,str) or len(text)>100 or any(ord(c)<32 for c in text) for text in value.values()):
            raise ValueError('账号显示资料内容错误')
    return values


def save_identity(data_dir, account_id, nickname, douyin_id):
    root=Path(data_dir)
    values=load_identities(root)
    values[account_id]={'nickname':nickname or '', 'douyin_id':douyin_id or ''}
    root.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=root,prefix='login-user-',suffix='.tmp',delete=False) as stream:
            temporary=stream.name
            json.dump(values,stream,ensure_ascii=False,indent=2)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,root/'login-user-identities.json')
    finally:
        if temporary and os.path.exists(temporary): os.unlink(temporary)
