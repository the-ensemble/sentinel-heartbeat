"""Sentinel heartbeat logger.

Runs every 5 minutes (EventBridge). Collects independent ground-truth signals
for conflict-zone regions and appends one JSONL line per run to S3.

Sections written per run:
  heartbeats  - AWS Lambda/API Gateway endpoints in-region
  probes      - RIPE Atlas connected/disconnected probe counts per country
  bgp         - RIPEstat routing visibility + announced prefix counts per ASN
  flights     - adsb.lol aircraft counts per zone (total, military, GPS-degraded)
  canaries    - raw TCP reachability of endpoints in regions with no Lambda
"""
import concurrent.futures
import datetime
import json
import socket
import time
import urllib.error
import urllib.request

import boto3

# --- AWS heartbeat endpoints ---
ENDPOINTS = {
    "me-central-1": "https://m6rjrc7hrc.execute-api.me-central-1.amazonaws.com/",
    "il-central-1": "https://c3icm0os4i.execute-api.il-central-1.amazonaws.com/",
    "ap-east-1": "https://jhxmflrny5.execute-api.ap-east-1.amazonaws.com/",
}

# --- BGP ASNs to monitor per country (major ISPs) ---
BGP_TARGETS = {
    "BH": [{"asn": "AS5416", "name": "STC Bahrain/Batelco"}],
    "AE": [{"asn": "AS5384", "name": "Etisalat/e&"}, {"asn": "AS15802", "name": "du/EITC"}],
    "IL": [{"asn": "AS8551", "name": "Bezeq International"}, {"asn": "AS1680", "name": "NV/Partner"}],
    "HK": [{"asn": "AS4515", "name": "PCCW/HKT"}, {"asn": "AS9304", "name": "HGC/Hutchison"}],
    "TW": [{"asn": "AS3462", "name": "HiNet/Chunghwa Telecom"}, {"asn": "AS9924", "name": "Taiwan Fixed Network"}],
}

# --- RIPE Atlas probe countries ---
PROBE_COUNTRIES = ["BH", "AE", "IL", "HK", "TW"]

# --- Flight zones: adsb.lol point queries (centre + radius, max 250 nm) ---
FLIGHT_ZONES = {
    "israel": {"lat": 31.25, "lon": 35.0, "radius_nm": 150},
    "persian_gulf": {"lat": 25.0, "lon": 53.0, "radius_nm": 250},
    "taiwan_strait": {"lat": 24.5, "lon": 119.5, "radius_nm": 250},
    "hong_kong": {"lat": 22.25, "lon": 114.0, "radius_nm": 60},
}

# --- TCP canaries for regions without a deployed heartbeat ---
CANARIES = {
    "me-south-1": {"label": "Bahrain", "host": "s3.me-south-1.amazonaws.com", "port": 443},
}

BUCKET = "atlas-sentinel-data"
PREFIX = "heartbeat-logs/"
RIPESTAT_BASE = "https://stat.ripe.net/data/routing-status/data.json"
ATLAS_BASE = "https://atlas.ripe.net/api/v2/probes/"
ADSB_BASE = "https://api.adsb.lol/v2/point"

# Per-source timeouts (seconds). RIPEstat routinely takes 12-18 s.
TIMEOUT_HEARTBEAT = 10
TIMEOUT_RIPESTAT = 25
TIMEOUT_ATLAS = 10
TIMEOUT_ADSB = 10
TIMEOUT_CANARY = 5

# adsb.lol rejects back-to-back requests with HTTP 429; space them out.
ADSB_SPACING_S = 1.5

# ADS-B Navigation Integrity Category at or below this is treated as GPS-degraded.
# NIC 7 = Rc < 0.2 NM; NIC <= 6 means position uncertainty > 0.5 NM, which for
# airliners in cruise almost always indicates GNSS interference.
GPS_DEGRADED_NIC = 6

USER_AGENT = "scout-sentinel/2.0 (+https://sonde.briansheppard.com)"

SECTIONS = ("heartbeats", "probes", "bgp", "flights", "canaries")


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def iso(dt):
    return dt.isoformat() + "Z"


def fetch_json(url, timeout, retries=0, backoff=2.0):
    """GET a JSON document. Retries on timeout or HTTP 429/5xx with a fixed backoff."""
    attempt = 0
    while True:
        try:
            req = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if attempt >= retries or e.code not in (429, 500, 502, 503, 504):
                raise
        except (TimeoutError, socket.timeout, urllib.error.URLError):
            if attempt >= retries:
                raise
        attempt += 1
        time.sleep(backoff)


def section_summary(results):
    ok = sum(1 for r in results if r.get("status") == "ok")
    return {"total": len(results), "ok": ok, "errors": len(results) - ok}


def reachability_summary(results):
    healthy = sum(1 for r in results if r.get("reachable"))
    return {"total": len(results), "healthy": healthy, "down": len(results) - healthy}


# ---------------------------------------------------------------- heartbeats

def check_one_heartbeat(region, url):
    entry = {"region": region, "url": url}
    t0 = time.monotonic()
    try:
        status, body = fetch_json(url, TIMEOUT_HEARTBEAT)
        entry["status_code"] = status
        entry["body"] = body
        entry["reachable"] = True
    except Exception as e:
        entry["reachable"] = False
        entry["error"] = str(e)
        entry["status_code"] = None
    entry["latency_ms"] = round((time.monotonic() - t0) * 1000)
    return entry


# ----------------------------------------------------------------------- bgp

def parse_bgp(data):
    """Extract the fields we track from a RIPEstat routing-status response."""
    d = data.get("data", {}) or {}
    vis = d.get("visibility", {}) or {}
    v4 = vis.get("v4", {}) or {}
    v6 = vis.get("v6", {}) or {}
    space = d.get("announced_space", {}) or {}

    def ratio(v):
        seeing = v.get("ris_peers_seeing", 0) or 0
        total = v.get("total_ris_peers", 0) or 0
        return round(seeing / total, 3) if total > 0 else 0

    return {
        "v4_visibility": ratio(v4),
        "v4_peers_seeing": v4.get("ris_peers_seeing", 0) or 0,
        "v4_total_peers": v4.get("total_ris_peers", 0) or 0,
        "v6_visibility": ratio(v6),
        "announced_prefixes": (space.get("v4", {}) or {}).get("prefixes", 0) or 0,
        "announced_prefixes_v6": (space.get("v6", {}) or {}).get("prefixes", 0) or 0,
        "observed_neighbours": d.get("observed_neighbours", 0) or 0,
    }


def check_one_bgp(country, target):
    entry = {"country": country, "asn": target["asn"], "name": target["name"]}
    try:
        url = f"{RIPESTAT_BASE}?resource={target['asn']}&sourceapp=scout-sentinel"
        _, data = fetch_json(url, TIMEOUT_RIPESTAT, retries=1, backoff=1.0)
        entry.update(parse_bgp(data))
        entry["status"] = "ok"
    except Exception as e:
        entry["status"] = "error"
        entry["error"] = str(e)
    return entry


# -------------------------------------------------------------------- probes

def check_one_probe(cc):
    entry = {"country": cc}
    try:
        _, d1 = fetch_json(f"{ATLAS_BASE}?country_code={cc}&status=1&page_size=1", TIMEOUT_ATLAS)
        entry["connected"] = d1.get("count", 0)
        _, d2 = fetch_json(f"{ATLAS_BASE}?country_code={cc}&status=2&page_size=1", TIMEOUT_ATLAS)
        entry["disconnected"] = d2.get("count", 0)
        entry["status"] = "ok"
    except Exception as e:
        entry["status"] = "error"
        entry["error"] = str(e)
    return entry


# ------------------------------------------------------------------- flights

def summarize_aircraft(aircraft):
    """Count aircraft, military-flagged aircraft, and GPS-degraded positions.

    adsb.lol dbFlags bit 0 = military. NIC is absent for MLAT-only targets, so
    the GPS metric is computed only over aircraft that report one.
    """
    military = 0
    sampled = 0
    degraded = 0
    for a in aircraft:
        if (a.get("dbFlags") or 0) & 1:
            military += 1
        nic = a.get("nic")
        if nic is not None:
            sampled += 1
            if nic <= GPS_DEGRADED_NIC:
                degraded += 1
    return {
        "aircraft_count": len(aircraft),
        "military_count": military,
        "gps_sampled": sampled,
        "gps_degraded_count": degraded,
    }


def check_one_zone(zone_name, cfg):
    entry = {"zone": zone_name, "source": "adsb.lol"}
    try:
        url = f"{ADSB_BASE}/{cfg['lat']}/{cfg['lon']}/{cfg['radius_nm']}"
        _, data = fetch_json(url, TIMEOUT_ADSB, retries=2, backoff=ADSB_SPACING_S)
        entry.update(summarize_aircraft(data.get("ac") or []))
        entry["status"] = "ok"
    except Exception as e:
        entry["status"] = "error"
        entry["error"] = str(e)
    return entry


def check_flights():
    """adsb.lol returns 429 for back-to-back requests, so zones run serially with spacing."""
    results = []
    for i, (zone, cfg) in enumerate(FLIGHT_ZONES.items()):
        if i:
            time.sleep(ADSB_SPACING_S)
        results.append(check_one_zone(zone, cfg))
    return results


# ------------------------------------------------------------------ canaries

def check_one_canary(region, cfg):
    entry = {"region": region, "label": cfg["label"], "host": cfg["host"], "port": cfg["port"]}
    t0 = time.monotonic()
    try:
        with socket.create_connection((cfg["host"], cfg["port"]), timeout=TIMEOUT_CANARY):
            pass
        entry["reachable"] = True
    except Exception as e:
        entry["reachable"] = False
        entry["error"] = str(e)
    entry["latency_ms"] = round((time.monotonic() - t0) * 1000)
    return entry


# ------------------------------------------------------------------- collect

def build_tasks():
    tasks = []
    for region, url in ENDPOINTS.items():
        tasks.append(("heartbeats", check_one_heartbeat, (region, url)))
    for cc in PROBE_COUNTRIES:
        tasks.append(("probes", check_one_probe, (cc,)))
    for country, targets in BGP_TARGETS.items():
        for t in targets:
            tasks.append(("bgp", check_one_bgp, (country, t)))
    # Flights run as one serial task (rate limited upstream); it returns a list.
    tasks.append(("flights", check_flights, ()))
    for region, cfg in CANARIES.items():
        tasks.append(("canaries", check_one_canary, (region, cfg)))
    return tasks


def collect():
    """Run every check concurrently and assemble the log entry sections."""
    tasks = build_tasks()
    results = {name: [] for name in SECTIONS}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = [(section, pool.submit(fn, *args)) for section, fn, args in tasks]
        for section, fut in futures:
            out = fut.result()
            if isinstance(out, list):
                results[section].extend(out)
            else:
                results[section].append(out)

    return {
        "heartbeats": {"results": results["heartbeats"], "summary": reachability_summary(results["heartbeats"])},
        "probes": {"results": results["probes"], "summary": section_summary(results["probes"])},
        "bgp": {"results": results["bgp"], "summary": section_summary(results["bgp"])},
        "flights": {"results": results["flights"], "summary": section_summary(results["flights"])},
        "canaries": {"results": results["canaries"], "summary": reachability_summary(results["canaries"])},
    }


def append_jsonl(s3, key, line):
    try:
        existing = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode()
    except s3.exceptions.NoSuchKey:
        existing = ""
    s3.put_object(Bucket=BUCKET, Key=key, Body=(existing + line).encode(), ContentType="application/x-ndjson")


def handler(event, context):
    now = utcnow()
    t0 = time.monotonic()

    log_entry = {"timestamp": iso(now)}
    log_entry.update(collect())
    log_entry["collect_seconds"] = round(time.monotonic() - t0, 2)

    key = f"{PREFIX}{now.strftime('%Y/%m/%d')}/heartbeats.jsonl"
    append_jsonl(boto3.client("s3"), key, json.dumps(log_entry) + "\n")

    summary = {name: log_entry[name]["summary"] for name in SECTIONS}
    print(json.dumps({"logged": key, "collect_seconds": log_entry["collect_seconds"], "summary": summary}))

    return {"statusCode": 200, "body": json.dumps({"logged": True, "key": key, "sections": list(log_entry.keys())})}
