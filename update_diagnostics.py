"""Bounded, local-only update records; no arbitrary exception or auth text."""
from datetime import datetime, timezone
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import re
import threading

from update_protocol import assert_plain_path, strict_json, UpdateError


STAGES={'check','confirm','preflight','download','verify','assemble','handoff',
        'wait_exit','backup','replace','startup','commit','recovery','failed','cancelled'}
ENUMS={'incremental_reason':{'selected','unavailable'},'package_type':{'full','incremental'},
       'category':{'network','signature','hash','baseline','space','permission','process',
                   'file_lock','handoff','replace','startup','recovery','unknown'},
       'result':{'started','ok','failed','cancelled','committed','restored','awaiting_manual_close'}}
NUMBERS={'pid','error_code','sequence','bytes','total_bytes','protocol','elapsed_ms'}


def diagnostic_error_fields(exc):
    """Classify our errors, but never retain their arbitrary text."""
    text=str(exc)
    fields={'category':'unknown'}
    for category,tokens in (
        ('process',('进程','实例')),('signature',('签名',)),('hash',('哈希',)),
        ('network',('网络','超时','下载不完整')),('space',('空间',)),
        ('permission',('权限','拒绝访问')),('baseline',('重叠','基线','目录结构')),
        ('handoff',('接管','握手')),('startup',('启动',)),('recovery',('恢复',))):
        if any(token in text for token in tokens): fields['category']=category; break
    code=getattr(exc,'winerror',None)
    if type(code) is not int: code=getattr(exc,'errno',None)
    match=re.search(r'Windows (?:查询)?错误 (\d+)',text)
    if match: code=int(match.group(1))
    if type(code) is int and code>=0: fields['error_code']=code
    match=re.search(r'软件进程 (\d+)',text)
    if match: fields['pid']=int(match.group(1))
    return fields


def sanitized_record(stage,fields):
    if stage not in STAGES: raise ValueError('Unknown update stage')
    record={'stage':stage}
    for name,value in fields.items():
        if name in NUMBERS and type(value) is int and 0<=value<2**63:
            record[name]=value
        elif name in ENUMS and isinstance(value,str) and value in ENUMS[name]:
            record[name]=value
        elif name in ('version','target_version') and isinstance(value,str) and re.fullmatch(r'\d{1,8}\.\d{1,8}\.\d{1,8}',value):
            record[name]=value
        elif name=='install_path' and isinstance(value,(str,Path)):
            record['install_fingerprint']=hashlib.sha256(str(value).casefold().encode('utf-8')).hexdigest()[:16]
    return record


class UpdateDiagnosticLog:
    def __init__(self,data_dir: Path,*,max_bytes=1024*1024):
        if type(max_bytes) is not int or not 256<=max_bytes<=1024*1024: raise ValueError('Invalid log size')
        self.root=assert_plain_path(Path(data_dir)/'update-diagnostics')
        self.max_bytes=max_bytes; self._lock=threading.RLock()

    def record(self,stage: str,fields: dict) -> None:
        entry=sanitized_record(stage,fields)
        entry['time']=datetime.now(timezone.utc).isoformat()
        raw=(json.dumps(entry,ensure_ascii=False,sort_keys=True)+'\n').encode('utf-8')
        if len(raw)>self.max_bytes: return
        # Diagnostics cannot turn a successful upgrade into a failure.
        try:
            with self._lock:
                assert_plain_path(self.root); self.root.mkdir(parents=True,exist_ok=True)
                current=assert_plain_path(self.root/'current.jsonl')
                if current.exists() and current.stat().st_size+len(raw)>self.max_bytes:
                    older=assert_plain_path(self.root/'older.jsonl'); previous=assert_plain_path(self.root/'previous.jsonl')
                    if previous.exists(): os.replace(previous,older)
                    os.replace(current,previous)
                with current.open('ab') as stream: stream.write(raw)
        except (OSError,UpdateError): pass

    def export(self,destination: Path,*,extra_roots=()) -> Path:
        records=deque(maxlen=2000)
        with self._lock:
            paths=[self.root/name for name in ('older.jsonl','previous.jsonl','current.jsonl')]
            for root in list(extra_roots)[:10]:
                paths.extend(Path(root)/'update-diagnostics'/name for name in ('older.jsonl','previous.jsonl','current.jsonl'))
            for item in paths:
                path=assert_plain_path(item)
                if not path.is_file() or path.stat().st_size>self.max_bytes: continue
                for line in path.read_bytes().splitlines():
                    try:
                        value=strict_json(line)
                        if not isinstance(value,dict): continue
                        safe=sanitized_record(value.get('stage'),value)
                        # Path fingerprints are fixed-format, never raw paths.
                        fingerprint=value.get('install_fingerprint','')
                        if isinstance(fingerprint,str) and re.fullmatch('[0-9a-f]{16}',fingerprint):
                            safe['install_fingerprint']=fingerprint
                        stamp=value.get('time','')
                        if isinstance(stamp,str) and re.fullmatch(r'[0-9T:.+\-]{20,40}',stamp): safe['time']=stamp
                        records.append(safe)
                    except (ValueError,UpdateError): continue
        destination=assert_plain_path(destination)
        raw=json.dumps({'format':1,'records':list(records)},ensure_ascii=False,indent=2)
        while len(raw.encode('utf-8'))>1024*1024 and records:
            records.popleft()
            raw=json.dumps({'format':1,'records':list(records)},ensure_ascii=False,indent=2)
        with destination.open('x',encoding='utf-8') as stream:
            stream.write(raw)
        return destination
