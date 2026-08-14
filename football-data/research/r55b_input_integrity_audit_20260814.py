#!/usr/bin/env python3
from __future__ import annotations
import base64, gzip, hashlib, json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
INP=ROOT/'football-data'/'research'/'r55b_inputs'
OUT=ROOT/'football-data'/'research'/'r55b_input_integrity_audit_20260814.json'

def audit(prefix:str):
    parts=sorted(INP.glob(prefix+'_*.b64part'))
    if not parts:
        return {'status':'NOT_PRESENT','parts':[]}
    meta=[]
    text=''
    for p in parts:
        b=p.read_bytes()
        meta.append({'name':p.name,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()})
        text += b.decode('ascii').strip()
    out={'parts':meta,'concat_chars':len(text),'concat_sha256':hashlib.sha256(text.encode()).hexdigest()}
    try:
        raw=base64.b64decode(text,validate=True)
        out['base64_status']='PASS'
        out['decoded_bytes']=len(raw)
        out['decoded_sha256']=hashlib.sha256(raw).hexdigest()
    except Exception as e:
        out['status']='BASE64_INCOMPLETE_OR_INVALID'
        out['base64_status']='FAIL'
        out['error']=type(e).__name__+': '+str(e)
        return out
    try:
        plain=gzip.decompress(raw)
        out['gzip_status']='PASS'
        out['plain_bytes']=len(plain)
        out['plain_sha256']=hashlib.sha256(plain).hexdigest()
    except Exception as e:
        out['status']='GZIP_INCOMPLETE_OR_INVALID'
        out['gzip_status']='FAIL'
        out['error']=type(e).__name__+': '+str(e)
        return out
    try:
        obj=json.loads(plain)
        out['json_status']='PASS'
        out['json_type']=type(obj).__name__
        if isinstance(obj,list): out['row_count']=len(obj)
        elif isinstance(obj,dict):
            out['top_keys']=sorted(obj.keys())[:50]
            for k in ('rows','calibration','target','frozen','data'):
                if isinstance(obj.get(k),list): out[k+'_count']=len(obj[k])
        out['status']='COMPLETE'
    except Exception as e:
        out['status']='JSON_INVALID'
        out['json_status']='FAIL'
        out['error']=type(e).__name__+': '+str(e)
    return out

payload={
 'schema':'R55B_INPUT_INTEGRITY_AUDIT_R1',
 'purpose':'technical recovery only; no alpha fitting; no new label access',
 'streams':{p:audit(p) for p in ('cal','calmin','target','targetmin')}
}
OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True))
