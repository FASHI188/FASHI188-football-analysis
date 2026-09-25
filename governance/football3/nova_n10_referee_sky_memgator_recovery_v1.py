#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from nova_n10_referee_sky_arquivo_feasibility_v1 import acquire_parents
from nova_n10_referee_sky_pit_binding_v1 import normalize_sky_identity

UTC = dt.timezone.utc


class SkyMemGatorRecoveryError(RuntimeError):
    pass


def req(cond: bool, msg: str) -> None:
    if not cond:
        raise SkyMemGatorRecoveryError(msg)


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


def build_timemap_url(base_url: str, target: str) -> str:
    base = base_url.rstrip("/")
    encoded = urllib.parse.quote(target, safe="")
    return f"{base}/timemap/json/{encoded}"


def fetch_json(url: str, source: dict[str, Any]) -> tuple[int, bytes, str, dict[str, str]]:
    req(host(url) == source["allowed_host"], "REQUEST_HOST")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": source["user_agent"],
            "Accept": "application/json,text/json;q=0.9,*/*;q=0.1",
        },
    )
    limit = int(source["max_response_bytes"])
    try:
        with urllib.request.urlopen(
            request,
            timeout=int(source["request_timeout_seconds"]),
            context=ssl.create_default_context(),
        ) as response:
            final = response.geturl()
            req(host(final) == source["allowed_host"], "REDIRECT_OUTSIDE_MEMGATOR")
            raw = response.read(limit + 1)
            req(len(raw) <= limit, "RESPONSE_TOO_LARGE")
            return (
                int(getattr(response, "status", 200)),
                raw,
                final,
                {k.lower(): v for k, v in response.headers.items()},
            )
    except urllib.error.HTTPError as exc:
        final = exc.geturl()
        req(host(final) == source["allowed_host"], "ERROR_REDIRECT_OUTSIDE_MEMGATOR")
        return int(exc.code), b"", final, {k.lower(): v for k, v in exc.headers.items()}


def parse_timemap(raw: bytes) -> tuple[str, list[dict[str, str]]]:
    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise SkyMemGatorRecoveryError("TIMEMAP_JSON_INVALID") from exc
    req(isinstance(obj, dict), "TIMEMAP_NOT_OBJECT")
    original = obj.get("original_uri")
    req(isinstance(original, str) and original.strip(), "ORIGINAL_URI_MISSING")
    mementos = obj.get("mementos")
    req(isinstance(mementos, dict), "MEMENTOS_OBJECT_MISSING")
    items = mementos.get("list")
    req(isinstance(items, list), "MEMENTOS_LIST_MISSING")
    out: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        when = item.get("datetime")
        uri = item.get("uri")
        if isinstance(when, str) and isinstance(uri, str):
            out.append({"datetime": when, "uri": uri})
    return original, out


def host_in(hostname: str, allowed: list[str]) -> bool:
    h = hostname.lower()
    return any(h == str(x).lower() for x in allowed)


def provider_family(
    snapshot_uri: str,
    recovery: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
) -> tuple[str, str | None]:
    h = host(snapshot_uri)
    if not h:
        return "UNKNOWN", None
    for family in recovery:
        if host_in(h, family["hosts"]):
            return "RECOVERY", str(family["id"])
    for family in excluded:
        if host_in(h, family["hosts"]):
            return "EXCLUDED", str(family["id"])
    return "UNKNOWN", None


def audit_mementos(
    original_uri: str,
    items: list[dict[str, str]],
    target: str,
    lower_utc: str,
    upper_utc: str,
    recovery: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
) -> dict[str, Any]:
    req(
        normalize_sky_identity(original_uri) == normalize_sky_identity(target),
        "ORIGINAL_URI_IDENTITY_MISMATCH",
    )
    lo = parse_z(lower_utc)
    hi = parse_z(upper_utc)
    req(lo < hi, "INVALID_PIT_WINDOW")
    audited: list[dict[str, Any]] = []
    for item in items:
        when = parse_memento_datetime(item["datetime"])
        family_status, family_id = provider_family(item["uri"], recovery, excluded)
        pit_ok = when is not None and lo <= when < hi
        eligible = bool(pit_ok and family_status == "RECOVERY")
        audited.append(
            {
                "datetime": item["datetime"],
                "capture_utc": when.isoformat().replace("+00:00", "Z") if when else None,
                "snapshot_uri": item["uri"],
                "snapshot_host": host(item["uri"]),
                "provider_status": family_status,
                "provider_family_id": family_id,
                "pit_time_ok": bool(pit_ok),
                "witness_pass": eligible,
            }
        )
    audited.sort(key=lambda x: (x["capture_utc"] or "", x["snapshot_host"], x["snapshot_uri"]))
    witnesses = [x for x in audited if x["witness_pass"]]
    excluded_rows = [x for x in audited if x["provider_status"] == "EXCLUDED"]
    unknown_rows = [x for x in audited if x["provider_status"] == "UNKNOWN"]
    return {
        "memento_n": len(audited),
        "eligible_recovery_witness_n": len(witnesses),
        "eligible_recovery_witnesses": witnesses,
        "excluded_provider_memento_n": len(excluded_rows),
        "unknown_provider_memento_n": len(unknown_rows),
        "audited_mementos": audited,
    }


def classify(pass_n: int, error_n: int, p: dict[str, Any]) -> tuple[str, str]:
    if pass_n > 0:
        return (
            p["decision_contract"]["positive_classification"],
            p["reasonable_subroutes"]["if_positive"],
        )
    if error_n > 0:
        return (
            p["decision_contract"]["fail_classification"],
            p["reasonable_subroutes"]["if_external_error"],
        )
    return (
        p["decision_contract"]["fail_classification"],
        p["reasonable_subroutes"]["if_complete_zero"],
    )


def run(registry: Path, out: Path, token: str) -> dict[str, Any]:
    p = json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"] == "DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY", "STATUS")
    req(p["exact_base"] == "6f69e9008015061cc4802a1df40b7d5565679b5b", "EXACT_BASE")

    ac = p["aggregator_contract_source"]
    req(ac["repository"] == "agntn/archives", "AGGREGATOR_CONTRACT_REPO")
    req(ac["commit"] == "37d69db28665afe70d1bacf7e704343fe0cf1b6a", "AGGREGATOR_CONTRACT_COMMIT")
    req(ac["path"] == "src/providers/memento.ts", "AGGREGATOR_CONTRACT_PATH")
    req(ac["git_blob_sha"] == "8f8b09cda3ee69cd8f6ab0e1be76aa4584c8b174", "AGGREGATOR_CONTRACT_BLOB")
    req(ac["default_base_url"] == "https://memgator.cs.odu.edu", "AGGREGATOR_BASE")

    previous = p["previous_routes"]["direct_matrix"]
    req(previous["clean_zero_provider_ids"] == ["waext.banq.qc.ca", "wayback.vefsafn.is"], "CLEAN_ZERO_SET")
    expected_failed = [
        "perma.cc", "warp.da.ndl.go.jp", "web.archive.org.au",
        "webarchiveweb.bac-lac.canada.ca", "webarchive.nrscotland.gov.uk",
        "webarchive.org.uk", "webarchive.parliament.uk",
    ]
    req(previous["failed_provider_ids"] == expected_failed, "FAILED_PROVIDER_SET")
    recovery = p["recovery_provider_families"]
    req([x["id"] for x in recovery] == expected_failed, "RECOVERY_PROVIDER_SET")
    excluded = p["excluded_provider_families"]
    excluded_ids = {x["id"] for x in excluded}
    req(
        excluded_ids == {
            "web.archive.org", "arquivo.pt", "wayback.archive-it.org",
            "archive.today", "waext.banq.qc.ca", "wayback.vefsafn.is",
        },
        "EXCLUDED_PROVIDER_SET",
    )

    h = p["hard_rules"]
    req(h["direct_failed_provider_endpoint_requery_allowed"] is False, "NO_DIRECT_REQUERY")
    req(h["webarchiv_at_requery_allowed"] is False, "NO_WEBARCHIV_REQUERY")
    req(h["excluded_provider_can_count_as_signal"] is False, "NO_EXCLUDED_SIGNAL")
    req(h["clean_zero_provider_can_count_as_signal"] is False, "NO_CLEAN_ZERO_SIGNAL")
    req(h["memento_target_fetch_allowed"] is False and h["replay_proxy_fetch_allowed"] is False, "NO_PLAYBACK")
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

    source = p["source"]
    reports: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    witnesses: list[dict[str, Any]] = []

    for rnd in samples:
        prow = pit_rows[rnd]
        srow = sky_rows[rnd]
        req(prow["binding_status"] == "FAIL", f"SAMPLE_ALREADY_PASS:R{rnd}")
        target = srow.get("sky_url")
        req(isinstance(target, str) and target.startswith("https://sport.sky.it/"), f"SKY_URL:R{rnd}")
        lower = prow["sky_visible_published_utc"]
        upper = prow["first_fixture_cutoff_utc"]
        req(parse_z(lower) < parse_z(upper), f"PIT_WINDOW:R{rnd}")
        q = build_timemap_url(source["base_url"], target)
        report: dict[str, Any] = {
            "round": rnd,
            "target_url": target,
            "lower_utc": lower,
            "upper_utc": upper,
            "query_url": q,
            "status": "UNSET",
            "http_status": None,
            "response_persisted": False,
            "replay_proxy_fetch_performed": False,
            "memento_target_fetch_performed": False,
            "witness_pass_n": 0,
        }
        try:
            status, raw, final, headers = fetch_json(q, source)
            report.update(
                {
                    "http_status": status,
                    "final_url": final,
                    "content_type": headers.get("content-type"),
                }
            )
            if status == 404:
                report.update(
                    {
                        "status": "CLEAN_ZERO_404",
                        "response_bytes": 0,
                        "response_sha256": sha256_bytes(b""),
                        "memento_n": 0,
                        "witness_pass_n": 0,
                    }
                )
            elif 200 <= status < 300:
                original_uri, items = parse_timemap(raw)
                audit = audit_mementos(
                    original_uri, items, target, lower, upper, recovery, excluded
                )
                passed = audit["eligible_recovery_witnesses"]
                report.update(
                    {
                        "status": "SUCCESS",
                        "response_bytes": len(raw),
                        "response_sha256": sha256_bytes(raw),
                        "original_uri": original_uri,
                        **audit,
                        "witness_pass_n": len(passed),
                    }
                )
                for item in passed:
                    witnesses.append({"round": rnd, **item})
            else:
                report["status"] = "HTTP_ERROR"
                report["error"] = f"HTTP_{status}"
                errors.append({"round": rnd, "error": report["error"]})
        except Exception as exc:
            report["status"] = "EXTERNAL_OR_CONTRACT_ERROR"
            report["error"] = f"{type(exc).__name__}:{exc}"[:800]
            errors.append({"round": rnd, "error": report["error"]})
        reports.append(report)

    positive_rounds = sorted({int(x["round"]) for x in witnesses})
    positive_provider_ids = sorted(
        {str(x["provider_family_id"]) for x in witnesses if x.get("provider_family_id")}
    )
    classification, next_step = classify(len(witnesses), len(errors), p)

    matrix = {
        "schema_version": "football3-nova-n10-referee-sky-memgator-recovery-matrix-v1",
        "sample_rounds": samples,
        "query_n": len(reports),
        "error_n": len(errors),
        "errors": errors,
        "witness_pass_n": len(witnesses),
        "positive_rounds": positive_rounds,
        "positive_provider_ids": positive_provider_ids,
        "reports": reports,
        "response_body_persisted": False,
        "memento_target_fetch_performed": False,
        "replay_proxy_fetch_performed": False,
        "timegate_fetch_performed": False,
    }
    out.mkdir(parents=True, exist_ok=True)
    matrix_bytes = (
        json.dumps(matrix, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    (out / "sky_memgator_recovery_matrix.json").write_bytes(matrix_bytes)

    receipt = {
        "schema_version": "football3-nova-n10-referee-sky-memgator-recovery-receipt-v1",
        "status": "N10_REFEREE_SKY_MEMGATOR_RECOVERY_COMPLETE",
        "classification": classification,
        "exact_base": p["exact_base"],
        "registry_sha256": sha256_bytes(registry.read_bytes()),
        "aggregator_contract_commit": ac["commit"],
        "aggregator_contract_blob_sha": ac["git_blob_sha"],
        "parent_provenance": parent_prov,
        "sample_rounds": samples,
        "query_n": len(reports),
        "error_n": len(errors),
        "errors": errors,
        "witness_pass_n": len(witnesses),
        "positive_rounds": positive_rounds,
        "positive_provider_ids": positive_provider_ids,
        "matrix_sha256": sha256_bytes(matrix_bytes),
        "direct_failed_provider_endpoint_requery_n": 0,
        "webarchiv_at_requery_n": 0,
        "excluded_provider_counted_as_signal_n": 0,
        "clean_zero_provider_counted_as_signal_n": 0,
        "response_body_persisted": False,
        "memento_target_fetch_performed": False,
        "replay_proxy_fetch_performed": False,
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
    (out / "sky_memgator_recovery_receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, sort_keys=True, ensure_ascii=False))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.registry, args.out, os.environ.get("GITHUB_TOKEN", ""))


if __name__ == "__main__":
    main()
