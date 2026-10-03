# Sentinel — Region Heartbeat Monitor

Multi-signal ground-truth health checks for conflict-zone infrastructure, independent of news reports and of the cloud providers' own status pages.

**Live dashboard:** [https://sonde.briansheppard.com](https://sonde.briansheppard.com)

## What This Is

A geopolitical early-warning system that samples several independent signal sources every 5 minutes and shows them side by side:

- **AWS Lambda heartbeats** in 3 conflict-zone regions behind API Gateways
- **TCP canary** for Bahrain (me-south-1), where no Lambda can run since the March 2026 strikes
- **BGP routing visibility** and announced-prefix counts for 9 major ISP ASNs across 5 countries (RIPEstat)
- **RIPE Atlas probe counts** (connected vs disconnected) in 5 countries
- **ADS-B airspace activity** in 4 zones via adsb.lol: aircraft, military aircraft, and GPS-degraded positions (a GNSS jamming signature)
- **Route 53 health checks** with CloudWatch alarms and SNS email alerts on the AWS endpoints
- **S3 JSONL log** of every sample, read by the dashboard API

Azure Function heartbeats were part of the original design. The Azure subscription is closed, so Azure is no longer collected or displayed. The function source stays in `azure/` and `azure-node/` for a future redeploy.

## Signal Sources

### AWS Heartbeats (3 regions)

| Region | Endpoint |
|---|---|
| UAE (me-central-1) | https://m6rjrc7hrc.execute-api.me-central-1.amazonaws.com/ |
| Israel (il-central-1) | https://c3icm0os4i.execute-api.il-central-1.amazonaws.com/ |
| Hong Kong (ap-east-1) | https://jhxmflrny5.execute-api.ap-east-1.amazonaws.com/ |

### Bahrain Canary

The logger opens a TCP connection to `s3.me-south-1.amazonaws.com:443` every run. The region has been unreachable since the March 1-2, 2026 drone strikes. The dashboard card flips from "CANARY — OFFLINE" to "CANARY — REACHABLE" automatically when the region returns.

### BGP Monitoring (9 ASNs, 5 countries)

| Country | ASN | Provider |
|---|---|---|
| Bahrain | AS5416 | STC Bahrain/Batelco |
| UAE | AS5384 | Etisalat/e& |
| UAE | AS15802 | du/EITC |
| Israel | AS8551 | Bezeq International |
| Israel | AS1680 | NV/Partner |
| Hong Kong | AS4515 | PCCW/HKT |
| Hong Kong | AS9304 | HGC/Hutchison |
| Taiwan | AS3462 | HiNet/Chunghwa Telecom |
| Taiwan | AS9924 | Taiwan Fixed Network |

Uses the RIPEstat `routing-status` call. Tracks IPv4 and IPv6 RIS peer visibility, announced prefix counts, and observed neighbours. The dashboard derives status from visibility (Degraded under 90%, Down under 50%) and from the prefix count against the 24-hour median (Degraded on a 20% withdrawal, Down on 50%). A failed RIPEstat lookup is shown as "No data" in purple, never as an outage.

### RIPE Atlas Probes (5 countries)

BH, AE, IL, HK, TW. Connected and disconnected probe counts via the Atlas API.

### ADS-B Airspace Activity (4 zones)

| Zone | Centre (lat, lon) | Radius |
|---|---|---|
| Israel | 31.25, 35.0 | 150 nm |
| Persian Gulf | 25.0, 53.0 | 250 nm |
| Taiwan Strait | 24.5, 119.5 | 250 nm |
| Hong Kong | 22.25, 114.0 | 60 nm |

Point queries against `api.adsb.lol/v2/point`. No account needed. Requests are spaced 1.5 s apart because the API returns HTTP 429 for bursts. Per zone the logger records total aircraft, aircraft flagged military in the adsb.lol database, and the count of positions whose Navigation Integrity Category is 6 or lower, which for airliners in cruise almost always indicates GNSS interference.

## Deployed Infrastructure

### AWS Resources

| Region | Resource | ID |
|---|---|---|
| me-central-1 | Lambda `region-heartbeat`, API Gateway `m6rjrc7hrc` | Health check `c3f9f441-5a37-462a-b6d2-0c31441539db`, alarm `ME-Central-1-UAE-Unhealthy` |
| il-central-1 | Lambda `region-heartbeat`, API Gateway `c3icm0os4i` | Health check `8fe97cb9-0007-4e90-839d-cdeb4331715d`, alarm `IL-Central-1-TelAviv-Unhealthy` |
| ap-east-1 | Lambda `region-heartbeat`, API Gateway `jhxmflrny5` | Health check `fb07f4da-cef8-41a7-91b5-afd9965dd1a8`, alarm `AP-East-1-HongKong-Unhealthy` |
| me-south-1 | No resources. The former Route 53 TCP health check has been deleted; the logger's TCP canary replaces it. | |
| us-east-1 | Lambda `heartbeat-logger` (Python 3.12, 256 MB, 120 s) | EventBridge rule `heartbeat-logger-schedule`, every 5 min |
| us-east-1 | Lambda `sentinel-dashboard-api` (Python 3.12, 256 MB, 15 s) | API Gateway HTTP API `dy5td5v3n7`, `GET /api/status` |
| us-east-1 | S3 `atlas-sentinel-data` | Logs at `heartbeat-logs/YYYY/MM/DD/heartbeats.jsonl`, about 1.3 MB/day |
| us-east-1 | S3 `sonde.briansheppard.com` | Dashboard `index.html` |
| us-east-1 | CloudFront `EBKQ3P9SKPPG2` (`d3isdvutafytq9.cloudfront.net`) | ACM cert `c6836739-2510-42bd-a3f3-cb84b643231b`, OAC `E2RT19QB6TGAD2` |
| us-east-1 | SNS `arn:aws:sns:us-east-1:290318879194:region-health-alerts` | Email alerts |
| global | IAM role `heartbeat-lambda-role` | Inline policy `heartbeat-s3-logging`; also needs `AWSLambdaBasicExecutionRole` for CloudWatch Logs |

DNS: Porkbun CNAME `sonde` → `d3isdvutafytq9.cloudfront.net`. AWS account `290318879194`.

## How It Works

1. **Real-time alerting.** Route 53 health checkers hit each AWS API Gateway endpoint every 30 seconds from multiple locations. Three consecutive failures raise a CloudWatch alarm, which publishes to SNS and emails bshepp@gmail.com.
2. **5-minute logging.** `heartbeat-logger` runs all checks concurrently in a thread pool (about 10 to 15 s wall time) and appends one JSON line to the day's file in S3.
3. **Dashboard API.** `sentinel-dashboard-api` reads today's and yesterday's files, returns the latest full sample as `current`, a slimmed 24-hour `timeline`, and a per-ASN `bgp_baseline` (24-hour medians). If the latest flight sample failed it carries forward the last successful one and sets `flights_as_of`.
4. **Dashboard.** Static page on S3 behind CloudFront. Fetches the API every 60 seconds. Shows a stale-data banner when the latest sample is more than 15 minutes old.

### Log Line Format

Each JSONL line has `timestamp`, `collect_seconds`, and five sections, each with `results` and `summary`:

| Section | Per-result fields |
|---|---|
| `heartbeats` | `region`, `reachable`, `status_code`, `latency_ms`, `body` |
| `probes` | `country`, `connected`, `disconnected`, `status` |
| `bgp` | `country`, `asn`, `name`, `status`, `v4_visibility`, `v4_peers_seeing`, `v4_total_peers`, `v6_visibility`, `announced_prefixes`, `announced_prefixes_v6`, `observed_neighbours` |
| `flights` | `zone`, `status`, `aircraft_count`, `military_count`, `gps_sampled`, `gps_degraded_count` |
| `canaries` | `region`, `label`, `host`, `port`, `reachable`, `latency_ms` |

Entries written before October 2026 have an `azure_heartbeats` section, lack `canaries`, and only carry `flights` every 20 minutes. The API and dashboard tolerate both shapes.

## Development

```powershell
python -m pytest tests -q          # unit tests for the pure logic in both Lambdas
```

To exercise the collector locally without writing to S3:

```powershell
python -c "import sys; sys.path.insert(0,'lambda'); import heartbeat_logger as hl, json; print(json.dumps({k: v['summary'] for k, v in hl.collect().items()}))"
```

## Deploying

`deploy.ps1` zips and uploads each component. It needs the AWS CLI configured for account 290318879194.

```powershell
.\deploy.ps1 logger                    # heartbeat-logger
.\deploy.ps1 api                       # sentinel-dashboard-api
.\deploy.ps1 dashboard                 # index.html to S3 + CloudFront invalidation
.\deploy.ps1 heartbeat -Region ap-east-1   # one regional heartbeat Lambda
.\deploy.ps1 all                       # logger + api + dashboard
```

### Azure (decommissioned)

Kept for reference. `azure/` is the Python function (UAE North, East Asia); `azure-node/` is the Node.js function used for Israel Central, which did not support Python on Linux Consumption. Redeploy with `func azure functionapp publish <app>` and add the endpoints back to the logger if the subscription is reopened.

## Context

Both Middle East AWS regions were physically attacked by drone strikes on March 1-2, 2026, and me-south-1 has been offline since. Israel (il-central-1) monitors the broader Middle East conflict zone. Hong Kong (ap-east-1) and the Taiwan BGP and probe signals serve as early warning for Taiwan Strait escalation. This system provides independent, multi-signal ground-truth verification of regional stability.
