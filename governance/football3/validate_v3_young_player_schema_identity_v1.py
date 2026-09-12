#!/usr/bin/env python3
import argparse, hashlib, json
from pathlib import Path
from urllib.request import Request, urlopen

REMOTE='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/dvc/'
DESCRIPTORS={
 'scraper':('c9ec80fd8b18310f7bded68fe92b67d3.dir',74),
 'api':('c829abceffc9d979752b46b5e1c947bc.dir',17),
}
TARGETS={
 'scraper':{'2025/players.json.gz','2025/clubs.json.gz','2025/appearances.json.gz'},
 'api':{'2025/transfers.json'},
}
MAX_DESCRIPTOR_BYTES=2_000_000

def descriptor_url(md5):
    h=md5[:-4]
    return f'{REMOTE}files/md5/{h[:2]}/{h[2:]}.dir'

def object_url(md5):
    if not isinstance(md5,str) or len(md5)!=32 or any(c not in '0123456789abcdef' for c in md5):
        raise ValueError('invalid object md5')
    return f'{REMOTE}files/md5/{md5[:2]}/{md5[2:]}'

def fetch(url):
    if not url.startswith(REMOTE) or not url.endswith('.dir'):
        raise ValueError('descriptor only')
    with urlopen(Request(url,headers={'User-Agent':'Football3-young-player-schema-freeze/1.0'}),timeout=30) as r:
        raw=r.read(MAX_DESCRIPTOR_BYTES+1)
    if len(raw)>MAX_DESCRIPTOR_BYTES:
        raise RuntimeError('descriptor too large')
    return raw

def head_size(md5):
    url=object_url(md5)
    req=Request(url,method='HEAD',headers={'User-Agent':'Football3-young-player-schema-freeze/1.0'})
    with urlopen(req,timeout=30) as r:
        value=r.headers.get('Content-Length')
    if value is None or not value.isdigit() or int(value)<=0:
        raise RuntimeError('HEAD Content-Length unavailable')
    return int(value),url

def parse(raw):
    obj=json.loads(raw)
    if not isinstance(obj,list):
        raise ValueError('descriptor list required')
    out=[]
    for x in obj:
        rel=x.get('relpath'); md5=x.get('md5'); size=x.get('size')
        if not isinstance(rel,str) or not isinstance(md5,str) or len(md5.replace('.dir',''))!=32:
            raise ValueError('bad row')
        if size is not None and (not isinstance(size,int) or size<0):
            raise ValueError('bad size')
        out.append({'relpath':rel,'md5':md5,'size':size})
    return out

def freeze(network=True):
    receipt={
      'schema_version':'football3-v3-young-player-object-hash-freeze-receipt-v1',
      'phase':'DESCRIPTOR_OBJECT_HASH_FREEZE_ONLY',
      'labels_opened':0,'training':False,'tuning':False,'result_or_goal_values_read':0,
      'referenced_dvc_data_objects_downloaded':0,'object_get_requests':0,'object_head_requests':0,
      'selected_objects':[],'data_ready':False,'formal_weight':0,'matrix_delta':0
    }
    if not network:
        receipt['decision']='OFFLINE_SYNTHETIC_ONLY'
        return receipt
    for source,(dmd5,n) in DESCRIPTORS.items():
        raw=fetch(descriptor_url(dmd5)); rows=parse(raw)
        if len(rows)!=n:
            receipt['decision']='STOP_DESCRIPTOR_CARDINALITY_DRIFT'
            return receipt
        by={r['relpath']:r for r in rows}
        missing=sorted(TARGETS[source]-set(by))
        if missing:
            receipt['decision']='STOP_TARGET_OBJECT_MISSING'; receipt['missing']=missing
            return receipt
        for rel in sorted(TARGETS[source]):
            r=dict(by[rel]); r['source']=source
            if r['size'] is None:
                size,url=head_size(r['md5']); receipt['object_head_requests']+=1
                r['size']=size; r['size_source']='HTTP_HEAD_CONTENT_LENGTH'; r['object_url']=url
            else:
                r['size_source']='DVC_DESCRIPTOR'
            receipt['selected_objects'].append(r)
    if len(receipt['selected_objects'])!=4 or any(x['size'] is None for x in receipt['selected_objects']):
        receipt['decision']='STOP_OBJECT_METADATA_INCOMPLETE'
    else:
        receipt['selected_objects_sha256']=hashlib.sha256(json.dumps(receipt['selected_objects'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        receipt['decision']='READY_TO_FREEZE_EXACT_OBJECT_HASHES_NO_DATA_OBJECT_DOWNLOAD'
    return receipt

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output',required=True); ap.add_argument('--offline',action='store_true'); a=ap.parse_args()
    r=freeze(not a.offline); Path(a.output).write_text(json.dumps(r,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'decision':r['decision'],'selected_n':len(r.get('selected_objects',[])),'downloads':r['referenced_dvc_data_objects_downloaded'],'head_requests':r.get('object_head_requests',0)},sort_keys=True))
if __name__=='__main__': main()
