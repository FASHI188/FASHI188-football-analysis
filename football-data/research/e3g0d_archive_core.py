"""Read-only GitHub Artifact and local archive primitives."""
from __future__ import annotations
import io,json,os,urllib.error,urllib.parse,urllib.request,zipfile
from pathlib import Path
from e3g0d_common import E3Error,iso,now,parse_utc,sha,xwrite
API="https://api.github.com";WORKFLOW="football-research-e3g0d-api-football-forward-collector.yml";ARCHIVE_SCHEMA="E3G0D-LOCAL-ARCHIVE-1.0"
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*_):return None
class GitHubReader:
    def __init__(self,repo,token,timeout=20):
        if "/" not in repo:raise E3Error("repository must be owner/name")
        if not str(token).strip():raise E3Error("GH_TOKEN is required for Artifact reads")
        self.repo=repo;self.token=str(token).strip();self.timeout=timeout
    def request(self,path):
        url=f"{API}{path}";p=urllib.parse.urlsplit(url)
        if p.scheme!="https" or p.hostname!="api.github.com":raise E3Error("GitHub API outside allowlist")
        return urllib.request.Request(url,headers={"Authorization":f"Bearer {self.token}","Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28","User-Agent":"FASHI188-e3g0d-archive/1.0"})
    def json(self,path):
        try:
            with urllib.request.urlopen(self.request(path),timeout=self.timeout) as r:value=json.loads(r.read())
        except urllib.error.HTTPError as exc:
            if exc.code==404:raise E3Error("GitHub resource not found on default branch") from None
            raise E3Error(f"GitHub API HTTP {exc.code}") from None
        except Exception as exc:raise E3Error("GitHub API read failed") from exc
        if not isinstance(value,dict):raise E3Error("unexpected GitHub payload")
        return value
    def artifacts(self,prefix="football-e3g0d-"):
        rows=self.json(f"/repos/{self.repo}/actions/artifacts?per_page=100").get("artifacts")
        if not isinstance(rows,list):raise E3Error("invalid Artifact list")
        return [dict(x) for x in rows if isinstance(x,dict) and str(x.get("name","")).startswith(prefix)]
    def artifact(self,artifact_id):return dict(self.json(f"/repos/{self.repo}/actions/artifacts/{artifact_id}"))
    def download(self,artifact_id):
        opener=urllib.request.build_opener(urllib.request.HTTPHandler(),urllib.request.HTTPSHandler(),NoRedirect())
        try:opener.open(self.request(f"/repos/{self.repo}/actions/artifacts/{artifact_id}/zip"),timeout=self.timeout);raise E3Error("missing download redirect")
        except urllib.error.HTTPError as exc:
            if exc.code not in {302,307}:raise E3Error(f"Artifact download HTTP {exc.code}") from None
            location=exc.headers.get("Location")
        p=urllib.parse.urlsplit(location or "");host=p.hostname or ""
        if p.scheme!="https" or not (host.endswith(".githubusercontent.com") or host.endswith(".github.com")):raise E3Error("download redirect outside allowlist")
        with urllib.request.urlopen(urllib.request.Request(location,headers={"User-Agent":"FASHI188-e3g0d-archive/1.0"}),timeout=self.timeout) as r:return r.read()
def manifest_path(root):return Path(root)/"local_archive_manifest.jsonl"
def rows(root):
    p=manifest_path(root)
    if not p.exists():return []
    try:return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    except Exception as exc:raise E3Error("local archive manifest damaged") from exc
def archived_ids(root):return {int(x["artifact_id"]) for x in rows(root)}
def append_manifest(root,row):
    if int(row["artifact_id"]) in archived_ids(root):raise E3Error("artifact already archived")
    p=manifest_path(root);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open("ab") as f:f.write(json.dumps(dict(row),sort_keys=True,separators=(",",":")).encode()+b"\n");f.flush();os.fsync(f.fileno())
def verify_zip(raw):
    try:
        z=zipfile.ZipFile(io.BytesIO(raw));bad=z.testzip()
        if bad:raise E3Error(f"ZIP CRC failed: {bad}")
        names=set(z.namelist());checked=0
        for name in names:
            if not name.endswith(".manifest.json"):continue
            m=json.loads(z.read(name));path=m.get("raw_response_path") or m.get("raw_payload_path");digest=m.get("raw_response_sha256") or m.get("raw_payload_sha256")
            if path and digest:
                found=[n for n in names if n.endswith(str(path))]
                if not found or sha(z.read(found[0]))!=str(digest):raise E3Error("raw SHA-256 link failed")
                checked+=1
        return {"zip_crc":"PASS","members":len(names),"raw_sha256_links_checked":checked}
    except zipfile.BadZipFile as exc:raise E3Error("invalid Artifact ZIP") from exc
def safe_name(s):return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(s))[:120]
def archive_one(reader,root,artifact_id):
    if artifact_id in archived_ids(root):raise E3Error("artifact already archived")
    meta=reader.artifact(artifact_id)
    if meta.get("expired"):raise E3Error("artifact expired")
    raw=reader.download(artifact_id);actual=sha(raw);expected=str(meta.get("digest") or "").removeprefix("sha256:")
    if not expected or actual!=expected:raise E3Error("Artifact SHA-256 mismatch or unavailable")
    check=verify_zip(raw);rel=Path("artifacts")/f"{artifact_id}__{safe_name(meta.get('name'))}__sha256_{actual}.zip";xwrite(Path(root)/rel,raw)
    row={"schema_version":ARCHIVE_SCHEMA,"artifact_id":artifact_id,"artifact_name":meta.get("name"),"artifact_created_at":meta.get("created_at"),"artifact_expires_at":meta.get("expires_at"),"github_digest":meta.get("digest"),"downloaded_sha256":actual,"archived_at_utc":iso(now()),"local_path":rel.as_posix(),"content_verification":check,"append_only":True,"github_artifact_deleted":False,"repository_modified":False}
    append_manifest(root,row);return row
