"""Bounded HTTPS client for the single approved API-Football host."""
from __future__ import annotations
import json,time,urllib.error,urllib.request
from e3g0d_common import E3Error,MAX_BACKOFF,MAX_RETRIES,MAX_TIMEOUT,HOST,api_url,now
MAX_BODY=10*1024*1024
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*_): raise E3Error("HTTP redirect refused")
class Client:
    def __init__(self,key,timeout,retries,backoff,budget,sleep=time.sleep,opener=None):
        key=str(key).strip()
        if not key: raise E3Error("API_FOOTBALL_KEY is required")
        if not 0<timeout<=MAX_TIMEOUT: raise E3Error("invalid timeout")
        if not 0<=retries<=MAX_RETRIES: raise E3Error("invalid retry count")
        if not 0<backoff<=MAX_BACKOFF: raise E3Error("invalid backoff cap")
        self._key=key;self.timeout=timeout;self.retries=retries;self.backoff=backoff;self.budget=budget;self.sleep=sleep
        self.opener=opener or urllib.request.build_opener(NoRedirect())
    def delay(self,attempt,retry_after=None):
        try:return min(max(float(retry_after),0.),self.backoff) if retry_after else min(float(2**attempt),self.backoff)
        except ValueError:return min(float(2**attempt),self.backoff)
    def get(self,endpoint,params):
        url=api_url(endpoint,params)
        for attempt in range(self.retries+1):
            self.budget.take(); requested=now()
            req=urllib.request.Request(url,headers={"x-apisports-key":self._key,"Accept":"application/json","User-Agent":"FASHI188-e3g0d/1.1"})
            try:
                with self.opener.open(req,timeout=self.timeout) as resp:
                    final=urllib.parse.urlsplit(resp.geturl())
                    if final.scheme!="https" or final.hostname!=HOST: raise E3Error("redirect outside allowlist")
                    raw=resp.read(MAX_BODY+1); observed=now()
                    if len(raw)>MAX_BODY: raise E3Error("response too large")
                    if self._key.encode() in raw: raise E3Error("response contained credential material")
                    try: payload=json.loads(raw)
                    except json.JSONDecodeError as exc: raise E3Error("provider returned non-JSON") from exc
                    if not isinstance(payload,dict): raise E3Error("provider returned non-object JSON")
                    if payload.get("errors"): raise E3Error("provider returned an error object")
                    headers={str(k):str(v) for k,v in resp.headers.items()}
                    rem=headers.get("x-ratelimit-requests-remaining") or headers.get("X-RateLimit-Requests-Remaining")
                    if rem is not None:
                        try:
                            if int(rem)<10: raise E3Error("provider quota reserve reached")
                        except ValueError as exc: raise E3Error("invalid rate-limit header") from exc
                    return raw,payload,requested,observed,int(resp.status),headers
            except E3Error: raise
            except urllib.error.HTTPError as exc:
                if 300<=exc.code<400: raise E3Error("HTTP redirect refused") from None
                if not (exc.code==429 or 500<=exc.code<=599) or attempt>=self.retries: raise E3Error(f"HTTP {exc.code} from provider") from None
                self.sleep(self.delay(attempt,exc.headers.get("Retry-After") if exc.headers else None))
            except (urllib.error.URLError,TimeoutError):
                if attempt>=self.retries: raise E3Error("network request failed") from None
                self.sleep(self.delay(attempt))
        raise E3Error("request failed")
