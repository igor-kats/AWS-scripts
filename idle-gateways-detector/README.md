# idle-gateways-detector

Detects idle NAT Gateways and Internet Gateways by analyzing CloudWatch traffic metrics over a configurable time window. Outputs a summary to the terminal and a detailed Excel report with per-gateway sheets.

## Prerequisites

- Python 3.10+
- AWS CLI configured with a profile (or default credentials)
- IAM permissions: `sts:GetCallerIdentity`, `ec2:DescribeNatGateways`, `ec2:DescribeInternetGateways`, `ec2:DescribeVpcs`, `cloudwatch:GetMetricStatistics`, `cloudwatch:ListMetrics`

## Install

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Analyze last 90 days (default)
python idle_gw.py --region us-east-1

# Use a specific profile and lookback period
python idle_gw.py --region eu-west-1 --profile my-profile --days 30

# Specify output file path
python idle_gw.py --region us-east-1 --output /tmp/report.xlsx
```

## Output

- **Terminal**: Per-gateway summary showing idle percentage, traffic totals, average rates, and gateway-specific metrics (connection stats for NAT, drop counts for IGW).
- **Excel file**: `Summary` sheet with one row per gateway, plus individual sheets with raw 6-hour metric datapoints per gateway.

## How It Works

1. Discovers all NAT Gateways and Internet Gateways in the region (with pagination)
2. Fetches CloudWatch metrics in 30-day chunks (to avoid API limits)
3. Computes idle percentage: ratio of 6-hour periods with zero traffic bytes/packets
4. Calculates traffic totals, average rates, and gateway-specific metrics
5. Exports everything to a multi-sheet Excel workbook

## Metrics Collected

**NAT Gateway**: BytesIn/Out, PacketsIn/Out, ConnectionAttemptCount, ConnectionEstablishedCount, ErrorPortAllocation, IdleTimeoutCount, ActiveConnectionCount, ConnectionEstablishedRate

**Internet Gateway**: BytesIn/Out, PacketsIn/Out, BytesDropCountBlackholeIPv4, BytesDropCountNoRouteIPv4, PacketsDropCountBlackholeIPv4, PacketsDropCountNoRouteIPv4
