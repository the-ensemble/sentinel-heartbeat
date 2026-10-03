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
    "ap-east-2": "https://8hhwdbtp3e.execute-api.ap-east-2.amazonaws.com/",
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
IODA_BASE = "https://api.ioda.inetintel.cc.gatech.edu/v2"
COUNTRY_RIS_BASE = "https://stat.ripe.net/data/country-resource-stats/data.json"

# IODA raw datasources we keep -> short key in the log. Others (loss/latency
# breakdowns) are nested objects and are skipped.
IODA_SERIES = {
    "bgp": "bgp",              # /24 blocks visible in BGP
    "ping-slash24": "ping",    # /24 blocks responding to active probing
    "merit-nt": "telescope",   # darknet telescope traffic (unique source IPs)
    "gtr-norm": "gtr_norm",    # Google Transparency Report traffic, normalised
}
IODA_LEVEL_RANK = {"normal": 0, "warning": 1, "critical": 2}
IODA_SIGNAL_WINDOW_S = 2 * 3600
IODA_ALERT_WINDOW_S = 24 * 3600
COUNTRY_RIS_DAYS = 8

# Per-source timeouts (seconds). RIPEstat routinely takes 12-18 s.
TIMEOUT_HEARTBEAT = 10
TIMEOUT_RIPESTAT = 25
TIMEOUT_ATLAS = 10
TIMEOUT_ADSB = 10
TIMEOUT_CANARY = 5
TIMEOUT_IODA = 15
TIMEOUT_COUNTRY_RIS = 20

# adsb.lol rejects back-to-back requests with HTTP 429; space them out.
ADSB_SPACING_S = 1.5

# ADS-B Navigation Integrity Category at or below this is treated as GPS-degraded.
# NIC 7 = Rc < 0.2 NM; NIC <= 6 means position uncertainty > 0.5 NM, which for
# airliners in cruise almost always indicates GNSS interference.
GPS_DEGRADED_NIC = 6

USER_AGENT = "scout-sentinel/2.0 (+https://sonde.briansheppard.com)"

SECTIONS = ("heartbeats", "probes", "bgp", "flights", "canaries", "ioda", "country_routing")


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


# ---------------------------------------------------------------------- ioda

def parse_ioda_signals(data):
    """Latest non-null value (and its timestamp) for each tracked IODA datasource."""
    out = {key: None for key in IODA_SERIES.values()}
    series_list = (data.get("data") or [[]])[0] or []
    for s in series_list:
        key = IODA_SERIES.get(s.get("datasource"))
        if not key:
            continue
        values = s.get("values") or []
        start = s.get("from") or 0
        step = s.get("step") or 0
        for i in range(len(values) - 1, -1, -1):
            v = values[i]
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                as_of = datetime.datetime.fromtimestamp(start + i * step, datetime.timezone.utc)
                out[key] = {"value": v, "as_of": iso(as_of.replace(tzinfo=None))}
                break
    return out


def parse_ioda_alerts(data):
    """Summarise IODA outage alerts: latest level per datasource and which are still active."""
    alerts = sorted(data.get("data") or [], key=lambda a: a.get("time") or 0)
    latest = {}
    for a in alerts:
        ds = a.get("datasource")
        if ds:
            latest[ds] = {"level": a.get("level"), "time": a.get("time"), "condition": a.get("condition")}
    active = {ds: v["level"] for ds, v in latest.items() if v["level"] and v["level"] != "normal"}
    worst = max(active.values(), key=lambda lvl: IODA_LEVEL_RANK.get(lvl, 0)) if active else None
    return {"count": len(alerts), "latest": latest, "active": active, "worst": worst}


def check_one_ioda(cc):
    entry = {"country": cc}
    now = int(time.time())
    try:
        _, sig = fetch_json(
            f"{IODA_BASE}/signals/raw/country/{cc}?from={now - IODA_SIGNAL_WINDOW_S}&until={now}",
            TIMEOUT_IODA, retries=1)
        entry["signals"] = parse_ioda_signals(sig)
        _, al = fetch_json(
            f"{IODA_BASE}/outages/alerts?entityType=country&entityCode={cc}"
            f"&from={now - IODA_ALERT_WINDOW_S}&until={now}",
            TIMEOUT_IODA, retries=1)
        entry["alerts"] = parse_ioda_alerts(al)
        entry["status"] = "ok"
    except Exception as e:
        entry["status"] = "error"
        entry["error"] = str(e)
    return entry


# ------------------------------------------------------- country routing (RIS)

def parse_country_ris(data):
    """Latest daily RIS counts for a country plus the previous day's for a delta."""
    stats = (data.get("data") or {}).get("stats") or []

    def pick(s):
        return {
            "stats_date": (s.get("stats_date") or "")[:10],
            "asns": s.get("asns_ris"),
            "v4_prefixes": s.get("v4_prefixes_ris"),
            "v6_prefixes": s.get("v6_prefixes_ris"),
        }

    if not stats:
        return {"stats_date": None, "asns": None, "v4_prefixes": None, "v6_prefixes": None, "prev": None}
    out = pick(stats[-1])
    out["prev"] = pick(stats[-2]) if len(stats) > 1 else None
    return out


def check_one_country_ris(cc):
    entry = {"country": cc}
    try:
        start = (utcnow() - datetime.timedelta(days=COUNTRY_RIS_DAYS)).strftime("%Y-%m-%d")
        url = f"{COUNTRY_RIS_BASE}?resource={cc}&resolution=1d&starttime={start}&sourceapp=scout-sentinel"
        _, data = fetch_json(url, TIMEOUT_COUNTRY_RIS, retries=1)
        entry.update(parse_country_ris(data))
        entry["status"] = "ok"
    except Exception as e:
        entry["status"] = "error"
        entry["error"] = str(e)
    return entry


def is_hourly_sample(now):
    """Country RIS stats are daily; sample them on the first run of each hour only."""
    return now.minute < 5


# ------------------------------------------------------------------- collect

def build_tasks(hourly=False):
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
    for cc in PROBE_COUNTRIES:
        tasks.append(("ioda", check_one_ioda, (cc,)))
    if hourly:
        for cc in PROBE_COUNTRIES:
            tasks.append(("country_routing", check_one_country_ris, (cc,)))
    return tasks


def collect(now=None):
    """Run every check concurrently and assemble the log entry sections."""
    now = now or utcnow()
    hourly = is_hourly_sample(now)
    tasks = build_tasks(hourly=hourly)
    results = {name: [] for name in SECTIONS}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = [(section, pool.submit(fn, *args)) for section, fn, args in tasks]
        for section, fut in futures:
            out = fut.result()
            if isinstance(out, list):
                results[section].extend(out)
            else:
                results[section].append(out)

    out = {
        "heartbeats": {"results": results["heartbeats"], "summary": reachability_summary(results["heartbeats"])},
        "probes": {"results": results["probes"], "summary": section_summary(results["probes"])},
        "bgp": {"results": results["bgp"], "summary": section_summary(results["bgp"])},
        "flights": {"results": results["flights"], "summary": section_summary(results["flights"])},
        "canaries": {"results": results["canaries"], "summary": reachability_summary(results["canaries"])},
        "ioda": {"results": results["ioda"], "summary": section_summary(results["ioda"])},
    }
    if hourly:
        out["country_routing"] = {
            "results": results["country_routing"],
            "summary": section_summary(results["country_routing"]),
        }
    return out


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
    log_entry.update(collect(now))
    log_entry["collect_seconds"] = round(time.monotonic() - t0, 2)

    key = f"{PREFIX}{now.strftime('%Y/%m/%d')}/heartbeats.jsonl"
    append_jsonl(boto3.client("s3"), key, json.dumps(log_entry) + "\n")

    summary = {name: log_entry[name]["summary"] for name in SECTIONS if name in log_entry}
    print(json.dumps({"logged": key, "collect_seconds": log_entry["collect_seconds"], "summary": summary}))

    return {"statusCode": 200, "body": json.dumps({"logged": True, "key": key, "sections": list(log_entry.keys())})}
