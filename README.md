# Region Heartbeat Monitor

Multi-cloud ground-truth health checks for conflict zones, independent of news reports, AWS Health Dashboard, or Azure Service Health.

## What This Is

A geopolitical early-warning system using multiple independent signal sources:
- **AWS Lambda heartbeats** in 3 conflict-zone regions behind API Gateways
- **Azure Function heartbeats** in 3 matching regions for multi-cloud redundancy
- **BGP routing visibility** for 9 major ISP ASNs across 5 countries
- **RIPE Atlas probe counts** (connected vs disconnected) in 5 countries
- **OpenSky flight density** across 4 bounding boxes (anonymous, rate-limited)
- **Route 53 health checks** with CloudWatch alarms and SNS email alerts
- **S3 JSONL logging** every 5 minutes with all signal data

## Signal Sources

### AWS Heartbeats (3 regions)

| Region | Endpoint |
|---|---|
| UAE (me-central-1) | https://m6rjrc7hrc.execute-api.me-central-1.amazonaws.com/ |
| Israel (il-central-1) | https://c3icm0os4i.execute-api.il-central-1.amazonaws.com/ |
| Hong Kong (ap-east-1) | https://jhxmflrny5.execute-api.ap-east-1.amazonaws.com/ |

### Azure Heartbeats (3 regions)

| Region | Endpoint |
|---|---|
| UAE North | https://sentinel-heartbeat-uae.azurewebsites.net/api/heartbeat |
| Israel Central | https://sentinel-heartbeat-israel.azurewebsites.net/api/heartbeat |
| East Asia (HK) | https://sentinel-heartbeat-hk.azurewebsites.net/api/heartbeat |

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

Uses RIPEstat routing-status API. Tracks IPv4 visibility, peer counts, and announced prefix counts.

### RIPE Atlas Probes (5 countries)

BH, AE, IL, HK, TW — connected/disconnected counts via Atlas API.

### OpenSky Flight Density (4 zones)

| Zone | Bounding Box (lat/lon) |
|---|---|
| Israel | 29-33.5°N, 34-36°E |
| Persian Gulf | 23-27°N, 49-57°E |
| Taiwan Strait | 22-27°N, 117-122°E |
| Hong Kong | 21.5-23°N, 113-115°E |

Anonymous access, rate-limited to every 20 minutes. Degrades gracefully on timeout.

## Deployed Infrastructure

### AWS Resources

#### UAE (me-central-1)

| Resource | ID / ARN |
|---|---|
| Lambda | `region-heartbeat` |
| API Gateway | `m6rjrc7hrc` |
| Health Check | `c3f9f441-5a37-462a-b6d2-0c31441539db` |
| Alarm | `ME-Central-1-UAE-Unhealthy` |

#### Israel (il-central-1)

| Resource | ID / ARN |
|---|---|
| Lambda | `region-heartbeat` |
| API Gateway | `c3icm0os4i` |
| Health Check | `8fe97cb9-0007-4e90-839d-cdeb4331715d` |
| Alarm | `IL-Central-1-TelAviv-Unhealthy` |

#### Bahrain Canary (me-south-1)

| Resource | ID / ARN |
|---|---|
| Health Check | `49fdfad8-98b3-4f6f-b165-bf23a803421e` |
| Alarm | `ME-South-1-Bahrain-Unhealthy` |

*No Lambda deployed — region destroyed by drone strikes (Mar 1-2, 2026). TCP health check against s3.me-south-1 serves as a recovery canary.*

#### Hong Kong (ap-east-1)

| Resource | ID / ARN |
|---|---|
| Lambda | `region-heartbeat` |
| API Gateway | `jhxmflrny5` |
| Health Check | `fb07f4da-cef8-41a7-91b5-afd9965dd1a8` |
| Alarm | `AP-East-1-HongKong-Unhealthy` |

#### Centralized Logger (us-east-1)

| Resource | ID / ARN |
|---|---|
| Lambda | `heartbeat-logger` (256MB, 120s timeout) |
| EventBridge Rule | `heartbeat-logger-schedule` (every 5 min) |
| S3 Logs | `s3://atlas-sentinel-data/heartbeat-logs/YYYY/MM/DD/heartbeats.jsonl` |
| IAM Policy | `heartbeat-s3-logging` (inline on heartbeat-lambda-role) |

#### Shared AWS Resources

| Resource | ID / ARN |
|---|---|
| SNS Topic | `arn:aws:sns:us-east-1:290318879194:region-health-alerts` |
| IAM Role | `heartbeat-lambda-role` |
| S3 Bucket | `atlas-sentinel-data` |

**AWS Account:** 290318879194

### Azure Resources

| Resource | Region | Type |
|---|---|---|
| Resource Group | UAE North | `heartbeat-monitors` |
| Function App | UAE North | `sentinel-heartbeat-uae` (Python 3.11, Linux Consumption) |
| Function App | Israel Central | `sentinel-heartbeat-israel` (Node.js 20, Windows Consumption) |
| Function App | East Asia | `sentinel-heartbeat-hk` (Python 3.11, Linux Consumption) |
| Storage Account | UAE North | `sentinelhbuae` |
| Storage Account | Israel Central | `sentinelhbisrael` |
| Storage Account | East Asia | `sentinelhbhk` |

*Israel Central doesn't support Python on Linux Consumption — uses Node.js on Windows instead.*

**Azure Subscription:** 9a4c89d6-db49-40a3-b8d1-d7153d1fa6c2

## How It Works

1. **Real-time alerting**: Route 53 health checkers hit each AWS API Gateway endpoint every 30 seconds from multiple global locations. If 3 consecutive checks fail → CloudWatch alarm → SNS → email to bshepp@gmail.com.
2. **Bahrain canary**: TCP health check against `s3.me-south-1.amazonaws.com:443` — currently failing due to drone strike damage (Mar 1-2, 2026). Will alarm-clear when the region recovers.
3. **5-minute logging**: `heartbeat-logger` Lambda polls all signal sources in order (fast → slow): AWS heartbeats → RIPE probes → Azure heartbeats → OpenSky flights (if 20-min gate) → BGP routing (slowest). Appends JSONL to S3.
4. **Log format**: Each JSONL line contains: `timestamp`, `heartbeats`, `probes`, `azure_heartbeats`, `flights` (when sampled), `bgp`.

## Deploying Updates

### AWS Heartbeat Lambda
```powershell
$tmp = "$env:TEMP\hb_deploy"; New-Item $tmp -ItemType Directory -Force | Out-Null
Copy-Item heartbeat\lambda\heartbeat.py "$tmp\heartbeat.py"
Compress-Archive -Path "$tmp\heartbeat.py" -DestinationPath "$tmp\heartbeat.zip" -Force
aws lambda update-function-code --function-name region-heartbeat --region <REGION> --zip-file "fileb://$tmp/heartbeat.zip"
```

### AWS Logger Lambda
```powershell
$tmp = "$env:TEMP\logger_deploy"; New-Item $tmp -ItemType Directory -Force | Out-Null
Copy-Item heartbeat\lambda\heartbeat_logger.py "$tmp\heartbeat_logger.py"
Compress-Archive -Path "$tmp\heartbeat_logger.py" -DestinationPath "$tmp\logger.zip" -Force
aws lambda update-function-code --function-name heartbeat-logger --region us-east-1 --zip-file "fileb://$tmp/logger.zip"
```

### Azure Functions (Python — UAE, HK)
```powershell
cd heartbeat\azure
func azure functionapp publish sentinel-heartbeat-uae --python
func azure functionapp publish sentinel-heartbeat-hk --python
```

### Azure Function (Node.js — Israel)
```powershell
cd heartbeat\azure-node
npm install
func azure functionapp publish sentinel-heartbeat-israel --javascript
```

## Dashboard

**Live:** [https://sonde.briansheppard.com](https://sonde.briansheppard.com)

Single-page dashboard showing all signals in real-time. Auto-refreshes every 60 seconds.

### Dashboard Architecture

```
Browser (sonde.briansheppard.com)
    |  HTTPS
CloudFront (CDN + SSL) — distribution EBKQ3P9SKPPG2
    |
S3 bucket (sonde.briansheppard.com/index.html)
    |  fetch() every 60s
API Gateway HTTP API (dy5td5v3n7, us-east-1)
    |
Lambda: sentinel-dashboard-api
    |  reads
S3: atlas-sentinel-data/heartbeat-logs/YYYY/MM/DD/heartbeats.jsonl
```

DNS: Porkbun CNAME `sonde` → `d3isdvutafytq9.cloudfront.net`

### Dashboard Resources

| Resource | ID / Details |
|---|---|
| Lambda | `sentinel-dashboard-api` (Python 3.12, 256MB, 15s) |
| API Gateway | `dy5td5v3n7` — `GET /api/status` |
| S3 Bucket | `sonde.briansheppard.com` |
| CloudFront | `EBKQ3P9SKPPG2` / `d3isdvutafytq9.cloudfront.net` |
| ACM Cert | `c6836739-2510-42bd-a3f3-cb84b643231b` |
| OAC | `E2RT19QB6TGAD2` |

### Updating the Dashboard

```powershell
# Update frontend
aws s3 cp heartbeat\dashboard\index.html s3://sonde.briansheppard.com/index.html --content-type "text/html; charset=utf-8"
aws cloudfront create-invalidation --distribution-id EBKQ3P9SKPPG2 --paths "/*"

# Update API Lambda
cd heartbeat\dashboard\api
python -c "import zipfile; z=zipfile.ZipFile('dashboard_api.zip','w'); z.write('dashboard_api.py'); z.close()"
aws lambda update-function-code --function-name sentinel-dashboard-api --zip-file fileb://dashboard_api.zip --region us-east-1
```

## Context

Both ME regions were physically attacked by drone strikes on March 1-2, 2026. AWS recommends migrating workloads out of the Middle East. Israel (il-central-1, Tel Aviv) monitors the broader Middle East conflict zone. Hong Kong (ap-east-1) serves as an early-warning canary for Taiwan Strait escalation. This system provides independent, multi-cloud, multi-signal ground-truth verification of regional stability.
