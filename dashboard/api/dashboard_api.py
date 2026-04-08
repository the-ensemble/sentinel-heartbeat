import json
import datetime
import boto3

BUCKET = "atlas-sentinel-data"
PREFIX = "heartbeat-logs/"


def slim_entry(entry):
    """Slim a full JSONL entry to just summaries for the timeline."""
    out = {"timestamp": entry.get("timestamp")}

    hb = entry.get("heartbeats", {})
    out["heartbeats"] = hb.get("summary", {})

    azure = entry.get("azure_heartbeats")
    if azure:
        out["azure"] = azure.get("summary", {})

    bgp = entry.get("bgp", {})
    out["bgp"] = bgp.get("summary", {})
    # Include per-ASN status for the detail table
    out["bgp_detail"] = [
        {
            "country": r.get("country"),
            "asn": r.get("asn"),
            "name": r.get("name"),
            "status": r.get("status"),
            "v4_visibility": r.get("v4_visibility"),
        }
        for r in bgp.get("results", [])
    ]

    probes = entry.get("probes", {})
    out["probes"] = probes.get("summary", {})
    out["probe_detail"] = [
        {
            "country": r.get("country"),
            "connected": r.get("connected"),
            "disconnected": r.get("disconnected"),
            "status": r.get("status"),
        }
        for r in probes.get("results", [])
    ]

    flights = entry.get("flights")
    if flights:
        out["flights"] = flights.get("summary", {})
        out["flight_detail"] = [
            {
                "zone": r.get("zone"),
                "aircraft_count": r.get("aircraft_count"),
                "status": r.get("status"),
            }
            for r in flights.get("results", [])
        ]

    return out


def read_day(s3, date_str):
    """Read all JSONL entries for a given date. Returns list of dicts."""
    key = f"{PREFIX}{date_str}/heartbeats.jsonl"
    try:
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode()
        entries = []
        for line in body.strip().split("\n"):
            if line.strip():
                entries.append(json.loads(line))
        return entries
    except s3.exceptions.NoSuchKey:
        return []
    except Exception:
        return []


def handler(event, context):
    now = datetime.datetime.utcnow()
    today = now.strftime("%Y/%m/%d")
    yesterday = (now - datetime.timedelta(days=1)).strftime("%Y/%m/%d")

    s3 = boto3.client("s3")

    # Read today + yesterday to always have 24h of data
    entries = read_day(s3, yesterday) + read_day(s3, today)

    # Keep only last 24 hours
    cutoff = (now - datetime.timedelta(hours=24)).isoformat() + "Z"
    entries = [e for e in entries if e.get("timestamp", "") >= cutoff]

    # Sort by timestamp
    entries.sort(key=lambda e: e.get("timestamp", ""))

    # Latest entry is the current state (full detail)
    current = entries[-1] if entries else None

    # Timeline: slim summaries, keep last 288 (24h at 5-min intervals)
    timeline = [slim_entry(e) for e in entries[-288:]]

    payload = {
        "current": current,
        "timeline": timeline,
        "generated_at": now.isoformat() + "Z",
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
