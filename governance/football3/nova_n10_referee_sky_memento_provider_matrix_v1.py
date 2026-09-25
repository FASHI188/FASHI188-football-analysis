#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from nova_n10_referee_sky_arquivo_feasibility_v1 import acquire_parents
from nova_n10_referee_sky_pit_binding_v1 import normalize_sky_identity

UTC = dt.timezone.utc


class SkyMementoProviderMatrixError(RuntimeError):
    pass


def req(cond: bool, msg: str) -> None:
    if not cond:
        raise SkyMementoProviderMatrixError(msg)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_z(value: str) -> dt.datetime:
    x = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if x.tzinfo is None:
        x = x.replace(tzinfo=UTC)
    return x.astimezone(UTC)


def parse_memento_datetime(value: str) -> dt.datetime | None:
    s = (value or "").strip()
    if not s:
        return None
    try:
        x = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if x.tzinfo is None:
            x = x.replace(tzinfo=UTC)
        return x.astimezone(UTC)
    except Exception:
        pass
    try:
        x = email.utils.parsedate_to_datetime(s)
        if x.tzinfo is None:
            x = x.replace(tzinfo=UTC)
        return x.astimezone(UTC)
    except Exception:
        return None


def host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


def build_timemap_url(base: str, original: str) -> str:
    req(base.endswith("/"), "TIMEMAP_BASE_MUST_END_SLASH")
    encoded = urllib.parse.quote(original, safe=":/?=&%")
    return base + encoded


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req_obj, fp, code, msg, headers, newurl):
        return None


def redirect_allowed(url: str, provider: dict[str, Any]) -> bool:
    p = urllib.parse.urlparse(url)
    allowed = {str(x).lower() for x in provider["allowed_hosts"]}
    return (
        p.scheme in {"http", "https"}
        and (p.hostname or "").lower() in allowed
        and "timemap" in (p.path or "").lower()
    )


def request_timemap(
    initial_url: str,
    provider: dict[str, Any],
    source: dict[str, Any],
) -> tuple[int, bytes, str, dict[str, str], int]:
    allowed = {str(x).lower() for x in provider["allowed_hosts"]}
    req(host(initial_url) in allowed, "REQUEST_HOST")
    max_redirects = int(source["max_redirects"])
    limit = int(source["max_response_bytes"])
    timeout = int(source["request_timeout_seconds"])
    opener = urllib.request.build_opener(
        NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    current = initial_url
    for redirect_n in range(max_redirects + 1):
        rq = urllib.request.Request(
            current,
            headers={
                "User-Agent": source["user_agent"],
                "Accept": "application/link-format,text/plain;q=0.9,*/*;q=0.1",
            },
        )
        try:
            with opener.open(rq, timeout=timeout) as r:
                final = r.geturl()
                req(host(final) in allowed, "FINAL_HOST")
                req("timemap" in (urllib.parse.urlparse(final).path or "").lower(), "FINAL_NOT_TIMEMAP")
                raw = r.read(limit + 1)
                req(len(raw) <= limit, "RESPONSE_TOO_LARGE")
                return (
                    int(getattr(r, "status", 200)),
                    raw,
                    final,
                    {k.lower(): v for k, v in r.headers.items()},
                    redirect_n,
                )
        except urllib.error.HTTPError as e:
            status = int(e.code)
            headers = {k.lower(): v for k, v in e.headers.items()}
            location = e.headers.get("Location")
            if 300 <= status < 400 and location:
                nxt = urllib.parse.urljoin(current, location)
                req(redirect_n < max_redirects, "REDIRECT_LIMIT")
                req(redirect_allowed(nxt, provider), "REDIRECT_OUTSIDE_TIMEMAP")
                current = nxt
                continue
            return status, b"", e.geturl(), headers, redirect_n
    raise SkyMementoProviderMatrixError("REDIRECT_LOOP")


_LINK_SPLIT = re.compile(r",\s*(?=<)")
_LINK_HEAD = re.compile(r"^\s*<([^>]+)>\s*(.*)$")
_PARAM = re.compile(r";\s*([A-Za-z0-9_-]+)\s*=\s*(?:\"([^\"]*)\"|([^;,\s]+))")


def parse_link_format(raw: bytes) -> list[dict[str, Any]]:
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return []
    out: list[dict[str, Any]] = []
    for part in _LINK_SPLIT.split(text):
        m = _LINK_HEAD.match(part)
        if not m:
            continue
        uri = m.group(1).strip()
        tail = m.group(2)
        params: dict[str, str] = {}
        for pm in _PARAM.finditer(tail):
            params[pm.group(1).lower()] = pm.group(2) if pm.group(2) is not None else pm.group(3)
        rel_tokens = [x.strip().lower() for x in params.get("rel", "").split() if x.strip()]
        out.append(
            {
                "uri": uri,
                "rel": rel_tokens,
                "datetime": params.get("datetime"),
            }
        )
    return out


def analyze_timemap(
    records: list[dict[str, Any]],
    target: str,
    lower_utc: str,
    upper_utc: str,
) -> dict[str, Any]:
    lo = parse_z(lower_utc)
    hi = parse_z(upper_utc)
    req(lo < hi, "INVALID_PIT_WINDOW")
    original_uris = sorted(
        {
            str(x["uri"])
            for x in records
            if "original" in x.get("rel", [])
        }
    )
    exact_originals = [
        u for u in original_uris
        if normalize_sky_identity(u) == normalize_sky_identity(target)
    ]
    req(bool(exact_originals), "TIMEMAP_ORIGINAL_MISSING_OR_MISMATCH")

    mementos: list[dict[str, Any]] = []
    for x in records:
        if "memento" not in x.get("rel", []):
            continue
        when = parse_memento_datetime(str(x.get("datetime") or ""))
        if when is None:
            continue
        in_pit = lo <= when < hi
        mementos.append(
            {
                "capture_utc": when.isoformat().replace("+00:00", "Z"),
                "memento_uri": x["uri"],
                "pit_time_ok": bool(in_pit),
            }
        )
    mementos.sort(key=lambda x: (x["capture_utc"], x["memento_uri"]))
    witnesses = [x for x in mementos if x["pit_time_ok"]]
    return {
        "original_relation_n": len(original_uris),
        "exact_original_relation_n": len(exact_originals),
        "memento_n": len(mementos),
        "pit_memento_n": len(witnesses),
        "pit_mementos": witnesses,
    }


def classify(
    witness_n: int,
    error_n: int,
    query_n: int,
    p: dict[str, Any],
) -> tuple[str, str]:
    if witness_n > 0:
        return (
            p["decision_contract"]["positive_classification"],
            p["reasonable_subroutes"]["if_positive"],
        )
    if error_n == 0:
        return (
            p["decision_contract"]["fail_classification"],
            p["reasonable_subroutes"]["if_complete_zero"],
        )
    if error_n == query_n:
        return (
            p["decision_contract"]["fail_classification"],
            p["reasonable_subroutes"]["if_external_error_only"],
        )
    return (
        p["decision_contract"]["fail_classification"],
        p["reasonable_subroutes"]["if_mixed_zero_and_error"],
    )


def run(registry: Path, out: Path, token: str) -> dict[str, Any]:
    p = json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"] == "DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY", "STATUS")
    req(p["exact_base"] == "8e454b16a22b8540deab8fa388d848f762e73b77", "EXACT_BASE")

    srcpin = p["memgator_registry_source"]
    req(srcpin["repository"] == "oduwsdl/MemGator", "REGISTRY_REPO")
    req(srcpin["commit"] == "6a222465b44503abe1bf80dc91109cef061a645b", "REGISTRY_COMMIT")
    req(srcpin["path"] == "docs/archives.json", "REGISTRY_PATH")
    req(srcpin["git_blob_sha"] == "e488ca99091d7eb6efc986524fe7fd9b12807fa5", "REGISTRY_BLOB")

    expected = p["expected_provider_ids"]
    providers = p["providers"]
    ids = [x["id"] for x in providers]
    req(ids == expected, "PROVIDER_SET_OR_ORDER")
    req(len(ids) == 9 and len(set(ids)) == 9, "PROVIDER_N")
    excluded = set(p["excluded_prior_providers"])
    req(not (set(ids) & excluded), "EXCLUDED_PROVIDER_PRESENT")
    req(excluded == {"archive.today", "arquivo.pt", "wayback.archive-it.org", "web.archive.org"}, "EXCLUDED_SET")

    h = p["hard_rules"]
    req(h["previous_source_requery_allowed"] is False, "NO_OLD_SOURCE_REQUERY")
    req(h["excluded_provider_query_allowed"] is False, "NO_EXCLUDED_PROVIDER")
    req(h["ignored_registry_entry_query_allowed"] is False, "NO_IGNORED_PROVIDER")
    req(h["memento_target_fetch_allowed"] is False and h["replay_fetch_allowed"] is False, "NO_REPLAY")
    req(h["timegate_fetch_allowed"] is False, "NO_TIMEGATE")
    req(h["response_body_persisted"] is False, "NO_RAW_PERSIST")
    req(h["article_body_read"] is False and h["referee_assignment_body_parsed"] is False, "NO_BODY")
    req(h["result_labels_read"] is False and h["score_values_read"] is False, "ZERO_LABEL")
    req(h["score_or_result_semantic_parse"] is False, "NO_RESULT_PARSE")
    req(h["match_result_payload_read"] is False and h["standings_payload_read"] is False and h["player_stats_payload_read"] is False, "NO_SPORT_PAYLOAD")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False, "NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False, "NO_PAID_SECRET")
    req(h["candidate_weight"] == 0 and h["matrix_delta"] == 0, "ZERO_WEIGHT")

    pit, sky, parent_prov = acquire_parents(p, token)
    pit_rows = {int(x["round"]): x for x in pit["rows"]}
    sky_rows = {int(x["round"]): x for x in sky["rows"]}
    samples = [int(x["round"]) for x in p["samples"]]
    req(samples == [8, 24, 34], "FROZEN_SAMPLES")

    source = p["source_contract"]
    reports: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    witnesses: list[dict[str, Any]] = []
    no_capture_statuses = {int(x) for x in source["no_capture_http_statuses"]}

    for provider in providers:
        for rnd in samples:
            prow = pit_rows[rnd]
            srow = sky_rows[rnd]
            req(prow["binding_status"] == "FAIL", f"SAMPLE_ALREADY_PASS:R{rnd}")
            target = srow.get("sky_url")
            req(isinstance(target, str) and target.startswith("https://sport.sky.it/"), f"SKY_URL:R{rnd}")
            lower = prow["sky_visible_published_utc"]
            upper = prow["first_fixture_cutoff_utc"]
            req(parse_z(lower) < parse_z(upper), f"PIT_WINDOW:R{rnd}")
            q = build_timemap_url(provider["timemap"], target)
            report: dict[str, Any] = {
                "provider_id": provider["id"],
                "provider_name": provider["name"],
                "round": rnd,
                "target_url": target,
                "lower_utc": lower,
                "upper_utc": upper,
                "query_url": q,
                "status": "UNSET",
                "http_status": None,
                "response_persisted": False,
                "memento_body_read": False,
                "witness_pass_n": 0,
                "witness_rows": [],
            }
            try:
                status, raw, final, headers, redirects = request_timemap(q, provider, source)
                report.update({
                    "http_status": status,
                    "final_url": final,
                    "redirect_n": redirects,
                    "content_type": headers.get("content-type"),
                })
                if status in no_capture_statuses:
                    report.update({
                        "status": "CLEAN_ZERO_NO_TIMEMAP",
                        "response_bytes": 0,
                        "response_sha256": sha256_bytes(b""),
                        "timemap_record_n": 0,
                        "identity_basis": "EXACT_REQUEST_URL_PLUS_PINNED_PROVIDER_404",
                    })
                elif 200 <= status < 300:
                    parsed = parse_link_format(raw)
                    audit = analyze_timemap(parsed, target, lower, upper)
                    report.update({
                        "status": "SUCCESS",
                        "response_bytes": len(raw),
                        "response_sha256": sha256_bytes(raw),
                        "timemap_record_n": len(parsed),
                        **audit,
                        "witness_pass_n": audit["pit_memento_n"],
                        "witness_rows": audit["pit_mementos"],
                    })
                    for w in audit["pit_mementos"]:
                        witnesses.append({
                            "provider_id": provider["id"],
                            "round": rnd,
                            **w,
                        })
                else:
                    report["status"] = "HTTP_ERROR"
                    report["error"] = f"HTTP_{status}"
                    errors.append({
                        "provider_id": provider["id"],
                        "round": rnd,
                        "error": report["error"],
                    })
            except Exception as exc:
                report["status"] = "EXTERNAL_OR_CONTRACT_ERROR"
                report["error"] = f"{type(exc).__name__}:{exc}"[:800]
                errors.append({
                    "provider_id": provider["id"],
                    "round": rnd,
                    "error": report["error"],
                })
            reports.append(report)

    query_n = len(providers) * len(samples)
    req(len(reports) == query_n == 27, "QUERY_N")
    positive_provider_ids = sorted({x["provider_id"] for x in witnesses})
    positive_rounds = sorted({int(x["round"]) for x in witnesses})
    error_provider_ids = sorted({x["provider_id"] for x in errors})

    provider_summaries = []
    for provider in providers:
        pid = provider["id"]
        rows = [x for x in reports if x["provider_id"] == pid]
        perrors = [x for x in rows if x["status"] in {"HTTP_ERROR", "EXTERNAL_OR_CONTRACT_ERROR"}]
        pwitness = sum(int(x.get("witness_pass_n") or 0) for x in rows)
        clean = len(perrors) == 0 and pwitness == 0
        provider_summaries.append({
            "provider_id": pid,
            "query_n": len(rows),
            "error_n": len(perrors),
            "witness_pass_n": pwitness,
            "clean_zero": clean,
        })

    clean_zero_provider_ids = sorted(
        x["provider_id"] for x in provider_summaries if x["clean_zero"]
    )
    classification, next_step = classify(len(witnesses), len(errors), query_n, p)

    matrix = {
        "schema_version": "football3-nova-n10-referee-sky-memento-provider-matrix-v1",
        "sample_rounds": samples,
        "provider_ids": ids,
        "provider_n": len(ids),
        "query_n": query_n,
        "error_n": len(errors),
        "errors": errors,
        "error_provider_ids": error_provider_ids,
        "clean_zero_provider_ids": clean_zero_provider_ids,
        "positive_provider_ids": positive_provider_ids,
        "positive_rounds": positive_rounds,
        "witness_pass_n": len(witnesses),
        "witnesses": witnesses,
        "provider_summaries": provider_summaries,
        "reports": reports,
        "response_body_persisted": False,
        "memento_body_read": False,
        "replay_fetch_performed": False,
        "timegate_fetch_performed": False,
    }
    out.mkdir(parents=True, exist_ok=True)
    matrix_bytes = (json.dumps(matrix, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    (out / "sky_memento_provider_matrix.json").write_bytes(matrix_bytes)

    receipt = {
        "schema_version": "football3-nova-n10-referee-sky-memento-provider-matrix-receipt-v1",
        "status": "N10_REFEREE_SKY_MEMENTO_PROVIDER_MATRIX_COMPLETE",
        "classification": classification,
        "exact_base": p["exact_base"],
        "registry_sha256": sha256_bytes(registry.read_bytes()),
        "memgator_registry_commit": srcpin["commit"],
        "memgator_registry_blob_sha": srcpin["git_blob_sha"],
        "parent_provenance": parent_prov,
        "sample_rounds": samples,
        "provider_ids": ids,
        "provider_n": len(ids),
        "query_n": query_n,
        "error_n": len(errors),
        "error_provider_ids": error_provider_ids,
        "clean_zero_provider_ids": clean_zero_provider_ids,
        "positive_provider_ids": positive_provider_ids,
        "positive_rounds": positive_rounds,
        "witness_pass_n": len(witnesses),
        "matrix_sha256": sha256_bytes(matrix_bytes),
        "excluded_prior_provider_query_n": 0,
        "ignored_registry_provider_query_n": 0,
        "response_body_persisted": False,
        "memento_body_read": False,
        "replay_fetch_performed": False,
        "timegate_fetch_performed": False,
        "article_body_read": False,
        "referee_assignment_body_parsed": False,
        "match_result_payload_read": False,
        "standings_payload_read": False,
        "player_stats_payload_read": False,
        "result_labels_read": 0,
        "score_values_read": 0,
        "training_performed": False,
        "scoring_performed": False,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
        "candidate_weight": 0,
        "matrix_delta": 0,
        "formal_available_at_proven": False,
        "fixture_level_binding_complete": False,
        "referee_oof_allowed": False,
        "next_step": next_step,
    }
    (out / "sky_memento_provider_matrix_receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, sort_keys=True, ensure_ascii=False))
    return receipt


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--registry", type=Path, required=True)
    a.add_argument("--out", type=Path, required=True)
    x = a.parse_args()
    run(x.registry, x.out, os.environ.get("GITHUB_TOKEN", ""))


if __name__ == "__main__":
    main()
