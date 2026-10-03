<#
.SYNOPSIS
  Deploy Sentinel components to AWS.

.EXAMPLE
  .\deploy.ps1 logger        # heartbeat-logger Lambda (us-east-1)
  .\deploy.ps1 api           # sentinel-dashboard-api Lambda (us-east-1)
  .\deploy.ps1 dashboard     # index.html to S3 + CloudFront invalidation
  .\deploy.ps1 heartbeat me-central-1   # region-heartbeat Lambda in one region
  .\deploy.ps1 all           # logger + api + dashboard
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("logger", "api", "dashboard", "heartbeat", "all")]
    [string]$Target,

    [string]$Region
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

$LoggerFunction = "heartbeat-logger"
$ApiFunction = "sentinel-dashboard-api"
$HeartbeatFunction = "region-heartbeat"
$HomeRegion = "us-east-1"
$SiteBucket = "sonde.briansheppard.com"
$Distribution = "EBKQ3P9SKPPG2"

function Zip-Single($Source, $Zip) {
    # Compress-Archive keeps the file at the archive root, which is what Lambda needs.
    if (Test-Path $Zip) { Remove-Item $Zip -Force }
    Compress-Archive -Path $Source -DestinationPath $Zip -Force
}

function Deploy-Lambda($Function, $Source, $Zip, $AwsRegion) {
    Write-Host "==> $Function ($AwsRegion) <- $(Split-Path -Leaf $Source)"
    Zip-Single $Source $Zip
    aws lambda update-function-code --function-name $Function --region $AwsRegion --zip-file "fileb://$Zip" --query "{State:State,LastUpdateStatus:LastUpdateStatus,CodeSize:CodeSize}" --output json
    aws lambda wait function-updated --function-name $Function --region $AwsRegion
    Write-Host "    done"
}

function Deploy-Logger {
    Deploy-Lambda $LoggerFunction "$Root\lambda\heartbeat_logger.py" "$Root\lambda\logger.zip" $HomeRegion
}

function Deploy-Api {
    Deploy-Lambda $ApiFunction "$Root\dashboard\api\dashboard_api.py" "$Root\dashboard\api\dashboard_api.zip" $HomeRegion
}

function Deploy-Dashboard {
    Write-Host "==> index.html -> s3://$SiteBucket"
    aws s3 cp "$Root\dashboard\index.html" "s3://$SiteBucket/index.html" --content-type "text/html; charset=utf-8" --cache-control "max-age=60"
    Write-Host "==> 404.html, robots.txt, favicons -> s3://$SiteBucket"
    aws s3 cp "$Root\dashboard\404.html" "s3://$SiteBucket/404.html" --content-type "text/html; charset=utf-8" --cache-control "max-age=300"
    aws s3 cp "$Root\dashboard\robots.txt" "s3://$SiteBucket/robots.txt" --content-type "text/plain; charset=utf-8" --cache-control "max-age=3600"
    aws s3 cp "$Root\dashboard\favicon.svg" "s3://$SiteBucket/favicon.svg" --content-type "image/svg+xml" --cache-control "max-age=86400"
    aws s3 cp "$Root\dashboard\favicon.ico" "s3://$SiteBucket/favicon.ico" --content-type "image/x-icon" --cache-control "max-age=86400"
    aws s3 cp "$Root\dashboard\apple-touch-icon.png" "s3://$SiteBucket/apple-touch-icon.png" --content-type "image/png" --cache-control "max-age=86400"
    $inv = aws cloudfront create-invalidation --distribution-id $Distribution --paths "/*" --query "Invalidation.Id" --output text
    Write-Host "    invalidation $inv"
}

function Deploy-Heartbeat {
    if (-not $Region) { throw "heartbeat target needs -Region (me-central-1, il-central-1, ap-east-1)" }
    Deploy-Lambda $HeartbeatFunction "$Root\lambda\heartbeat.py" "$Root\lambda\heartbeat.zip" $Region
}

switch ($Target) {
    "logger"    { Deploy-Logger }
    "api"       { Deploy-Api }
    "dashboard" { Deploy-Dashboard }
    "heartbeat" { Deploy-Heartbeat }
    "all"       { Deploy-Logger; Deploy-Api; Deploy-Dashboard }
}
