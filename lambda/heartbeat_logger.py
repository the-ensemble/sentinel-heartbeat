import json
import datetime
import urllib.request
import boto3

# --- AWS heartbeat endpoints ---
ENDPOINTS = {
    "me-central-1": "https://m6rjrc7hrc.execute-api.me-central-1.amazonaws.com/",
    "il-central-1": "https://c3icm0os4i.execute-api.il-central-1.amazonaws.com/",
    "ap-east-1": "https://jhxmflrny5.execute-api.ap-east-1.amazonaws.com/",
}

# --- Azure heartbeat endpoints ---
AZURE_ENDPOINTS = {
    "uaenorth": "https://sentinel-heartbeat-uae.azurewebsites.net/api/heartbeat",
    "israelcentral": "https://sentinel-heartbeat-israel.azurewebsites.net/api/heartbeat",
    "eastasia": "https://sentinel-heartbeat-hk.azurewebsites.net/api/heartbeat",
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

# --- OpenSky bounding boxes ---
FLIGHT_ZONES = {
    "israel": {"lamin": 29, "lomin": 34, "lamax": 33.5, "lomax": 36},
    "persian_gulf": {"lamin": 23, "lomin": 49, "lamax": 27, "lomax": 57},
    "taiwan_strait": {"lamin": 22, "lomin": 117, "lamax": 27, "lomax": 122},
    "hong_kong": {"lamin": 21.5, "lomin": 113, "lamax": 23, "lomax": 115},
}

BUCKET = "atlas-sentinel-data"
PREFIX = "heartbeat-logs/"
RIPESTAT_BASE = "https://stat.ripe.net/data/routing-status/data.json"
ATLAS_BASE = "https://atlas.ripe.net/api/v2/probes/"
OPENSKY_API_URL = "https://opensky-network.org/api/states/all"


def check_heartbeats():
    results = []
    for region, url in ENDPOINTS.items():
        entry = {"region": region, "url": url}
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                entry["status_code"] = resp.status
                entry["body"] = json.loads(resp.read().decode())
                entry["reachable"] = True
        except Exception as e:
            entry["reachable"] = False
            entry["error"] = str(e)
            entry["status_code"] = None
        results.append(entry)
    return {
        "results": results,
        "summary": {
            "total": len(results),
            "healthy": sum(1 for r in results if r["reachable"]),
            "down": sum(1 for r in results if not r["reachable"]),
        },
    }


def check_bgp():
    results = []
    for country, asns in BGP_TARGETS.items():
        for target in asns:
            asn_num = target["asn"].replace("AS", "")
            entry = {"country": country, "asn": target["asn"], "name": target["name"]}
            try:
                url = f"{RIPESTAT_BASE}?resource=AS{asn_num}&sourceapp=scout-sentinel"
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode())
                    vis = data.get("data", {}).get("visibility", {}).get("v4", {})
                    seeing = vis.get("ris_peers_seeing", 0)
                    total = vis.get("total_ris_peers", 1)
                    entry["v4_visibility"] = round(seeing / total, 3) if total > 0 else 0
                    entry["v4_peers_seeing"] = seeing
                    entry["v4_total_peers"] = total
                    prefixes = data.get("data", {}).get("announced_prefixes", [])
                    entry["announced_prefixes"] = len(prefixes) if isinstance(prefixes, list) else 0
                    entry["status"] = "ok"
            except Exception as e:
                entry["status"] = "error"
                entry["error"] = str(e)
            results.append(entry)
    return {
        "results": results,
        "summary": {
            "total": len(results),
            "ok": sum(1 for r in results if r.get("status") == "ok"),
            "errors": sum(1 for r in results if r.get("status") == "error"),
        },
    }


def check_probes():
    results = []
    for cc in PROBE_COUNTRIES:
        entry = {"country": cc}
        try:
            url = f"{ATLAS_BASE}?country_code={cc}&status=1&page_size=1"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                entry["connected"] = data.get("count", 0)

            url2 = f"{ATLAS_BASE}?country_code={cc}&status=2&page_size=1"
            req2 = urllib.request.Request(url2, method="GET")
            with urllib.request.urlopen(req2, timeout=10) as resp2:
                data2 = json.loads(resp2.read().decode())
                entry["disconnected"] = data2.get("count", 0)

            entry["status"] = "ok"
        except Exception as e:
            entry["status"] = "error"
            entry["error"] = str(e)
        results.append(entry)
    return {
        "results": results,
        "summary": {
            "total": len(results),
            "ok": sum(1 for r in results if r.get("status") == "ok"),
            "errors": sum(1 for r in results if r.get("status") == "error"),
        },
    }


def check_azure():
    if not AZURE_ENDPOINTS:
        return None
    results = []
    for region, url in AZURE_ENDPOINTS.items():
        entry = {"region": region, "url": url}
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                entry["status_code"] = resp.status
                entry["body"] = json.loads(resp.read().decode())
                entry["reachable"] = True
        except Exception as e:
            entry["reachable"] = False
            entry["error"] = str(e)
            entry["status_code"] = None
        results.append(entry)
    return {
        "results": results,
        "summary": {
            "total": len(results),
            "healthy": sum(1 for r in results if r["reachable"]),
            "down": sum(1 for r in results if not r["reachable"]),
        },
    }


def check_flights():
    # Rate limit: only run at minutes 0, 20, 40 to stay under 400 anonymous credits/day
    # (72 invocations/day × 5 credits = 360 credits)
    minute = datetime.datetime.utcnow().minute
    if minute % 20 >= 5:  # 5-min tolerance for EventBridge jitter
        return None

    results = []
    for zone_name, box in FLIGHT_ZONES.items():
        entry = {"zone": zone_name}
        try:
            url = (f"{OPENSKY_API_URL}?lamin={box['lamin']}&lomin={box['lomin']}"
                   f"&lamax={box['lamax']}&lomax={box['lomax']}")
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                states = data.get("states") or []
                entry["aircraft_count"] = len(states)
                entry["status"] = "ok"
        except Exception as e:
            entry["status"] = "error"
            entry["error"] = str(e)
        results.append(entry)
    return {
        "results": results,
        "summary": {
            "total": len(results),
            "ok": sum(1 for r in results if r.get("status") == "ok"),
            "errors": sum(1 for r in results if r.get("status") == "error"),
        },
    }


def handler(event, context):
    now = datetime.datetime.utcnow()
    date_key = now.strftime("%Y/%m/%d")

    log_entry = {
        "timestamp": now.isoformat() + "Z",
        "heartbeats": check_heartbeats(),
        "probes": check_probes(),
    }

    azure_data = check_azure()
    if azure_data is not None:
        log_entry["azure_heartbeats"] = azure_data

    flights_data = check_flights()
    if flights_data is not None:
        log_entry["flights"] = flights_data

    # BGP last — RIPEstat is slowest, can eat the clock
    log_entry["bgp"] = check_bgp()

    log_line = json.dumps(log_entry) + "\n"

    s3 = boto3.client("s3")
    key = f"{PREFIX}{date_key}/heartbeats.jsonl"

    try:
        existing = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode()
    except Exception:
        existing = ""

    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=(existing + log_line).encode(),
        ContentType="application/x-ndjson",
    )

    return {
        "statusCode": 200,
        "body": json.dumps({"logged": True, "key": key, "sections": list(log_entry.keys())}),
    }
