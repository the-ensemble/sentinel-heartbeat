"""Sentinel dashboard API.

GET /api/status -> {
    current:       latest full JSONL entry (flights carried forward from the
                   last successful sample when the latest one failed),
    timeline:      last 24h of slimmed entries (summaries + a few scalars),
    bgp_baseline:  per-ASN 24h medians of visibility and announced prefixes,
    generated_at, data_age_s, entries_count
}
"""
import datetime
import json
import statistics

import boto3

BUCKET = "atlas-sentinel-data"
PREFIX = "heartbeat-logs/"
WINDOW_HOURS = 24
MAX_TIMELINE = 288  # 24h at 5-minute cadence


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def parse_ts(ts):
    return datetime.datetime.fromisoformat(ts.rstrip("Z"))


def slim_entry(entry):
    """Reduce a full JSONL entry to what the 24h timeline needs."""
    out = {"timestamp": entry.get("timestamp")}
    out["heartbeats"] = (entry.get("heartbeats") or {}).get("summary", {})
    out["probes"] = (entry.get("probes") or {}).get("summary", {})

    bgp = entry.get("bgp") or {}
    out["bgp"] = bgp.get("summary", {})
    vis = [r.get("v4_visibility") for r in bgp.get("results", [])
           if r.get("status") == "ok" and r.get("v4_visibility") is not None]
    out["bgp_min_visibility"] = min(vis) if vis else None

    flights = entry.get("flights")
    if flights:
        out["flights"] = flights.get("summary", {})
        ok = [r for r in flights.get("results", []) if r.get("status") == "ok"]
        out["flights_totals"] = {
            "aircraft": sum(r.get("aircraft_count") or 0 for r in ok),
            "military": sum(r.get("military_count") or 0 for r in ok),
            "gps_sampled": sum(r.get("gps_sampled") or 0 for r in ok),
            "gps_degraded": sum(r.get("gps_degraded_count") or 0 for r in ok),
        }

    canaries = entry.get("canaries") or {}
    out["canaries"] = {r.get("region"): bool(r.get("reachable")) for r in canaries.get("results", [])}

    ioda = entry.get("ioda")
    if ioda:
        out["ioda"] = ioda.get("summary", {})
        levels = [(r.get("alerts") or {}).get("worst") for r in ioda.get("results", []) if r.get("status") == "ok"]
        out["ioda_worst"] = max((l for l in levels if l), key=lambda l: IODA_LEVEL_RANK.get(l, 0), default=None)
    return out


IODA_LEVEL_RANK = {"warning": 1, "critical": 2}

# Sections the API carries forward from the last successful sample when the
# latest entry lacks them or they failed.
CARRY_FORWARD_SECTIONS = ("flights", "country_routing")


def section_succeeded(entry, section):
    return bool(((entry.get(section) or {}).get("summary") or {}).get("ok"))


def carry_forward(entries, section):
    """Patch the latest entry's section from the most recent entry where it succeeded."""
    if not entries:
        return None
    current = entries[-1]
    if section_succeeded(current, section):
        return current
    for e in reversed(entries[:-1]):
        if section_succeeded(e, section):
            current[section] = e[section]
            current[f"{section}_as_of"] = e["timestamp"]
            break
    return current


def carry_forward_flights(entries):
    return carry_forward(entries, "flights")


def bgp_baseline(entries):
    """Per-ASN medians over the window, used to spot prefix withdrawals and visibility drops."""
    prefixes = {}
    visibility = {}
    for e in entries:
        for r in (e.get("bgp") or {}).get("results", []):
            if r.get("status") != "ok":
                continue
            asn = r.get("asn")
            # Zero is never a real baseline for a live ISP; legacy entries recorded 0 by mistake.
            if r.get("announced_prefixes"):
                prefixes.setdefault(asn, []).append(r["announced_prefixes"])
            if r.get("v4_visibility") is not None:
                visibility.setdefault(asn, []).append(r["v4_visibility"])
    out = {}
    for asn in set(prefixes) | set(visibility):
        p = prefixes.get(asn, [])
        v = visibility.get(asn, [])
        out[asn] = {
            "prefixes": int(round(statistics.median(p))) if p else None,
            "visibility": round(statistics.median(v), 3) if v else None,
            "samples": max(len(p), len(v)),
        }
    return out


def read_day(s3, date_str):
    key = f"{PREFIX}{date_str}/heartbeats.jsonl"
    try:
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode()
    except Exception:
        return []
    entries = []
    for line in body.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def handler(event, context):
    now = utcnow()
    today = now.strftime("%Y/%m/%d")
    yesterday = (now - datetime.timedelta(days=1)).strftime("%Y/%m/%d")

    s3 = boto3.client("s3")
    entries = read_day(s3, yesterday) + read_day(s3, today)

    cutoff = (now - datetime.timedelta(hours=WINDOW_HOURS)).isoformat() + "Z"
    entries = [e for e in entries if e.get("timestamp", "") >= cutoff]
    entries.sort(key=lambda e: e.get("timestamp", ""))
    entries = entries[-MAX_TIMELINE:]

    current = entries[-1] if entries else None
    for section in CARRY_FORWARD_SECTIONS:
        current = carry_forward(entries, section)
    data_age_s = None
    if current and current.get("timestamp"):
        data_age_s = int((now - parse_ts(current["timestamp"])).total_seconds())

    payload = {
        "current": current,
        "timeline": [slim_entry(e) for e in entries],
        "bgp_baseline": bgp_baseline(entries),
        "generated_at": now.isoformat() + "Z",
        "data_age_s": data_age_s,
        "entries_count": len(entries),
    }

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "https://sonde.briansheppard.com",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Cache-Control": "max-age=30",
        },
        "body": json.dumps(payload),
    }
