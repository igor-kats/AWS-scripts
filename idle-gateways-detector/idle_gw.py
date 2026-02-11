#!/usr/bin/env python3
"""CLI tool for detecting idle NAT Gateways and Internet Gateways via CloudWatch metrics."""

import argparse
import datetime
from datetime import timezone
from pathlib import Path

import boto3
import pandas as pd
from openpyxl.utils import get_column_letter

NAT_METRICS = [
    "BytesInFromDestination",
    "BytesInFromSource",
    "BytesOutToDestination",
    "BytesOutToSource",
    "PacketsInFromDestination",
    "PacketsInFromSource",
    "PacketsOutToDestination",
    "PacketsOutToSource",
    "ConnectionAttemptCount",
    "ConnectionEstablishedCount",
    "ErrorPortAllocation",
    "IdleTimeoutCount",
    "ActiveConnectionCount",
    "ConnectionEstablishedRate",
]

IGW_METRICS = [
    "BytesInFromDestination",
    "BytesOutToDestination",
    "PacketsInFromDestination",
    "PacketsOutToDestination",
    "BytesDropCountBlackholeIPv4",
    "BytesDropCountNoRouteIPv4",
    "PacketsDropCountBlackholeIPv4",
    "PacketsDropCountNoRouteIPv4",
]

PERIOD_SECONDS = 21600  # 6 hours


# -- AWS helpers ---------------------------------------------------------------


def make_session(profile: str | None, region: str) -> boto3.Session:
    kwargs: dict = {"region_name": region}
    if profile:
        kwargs["profile_name"] = profile
    return boto3.Session(**kwargs)


def get_account_id(session: boto3.Session) -> str:
    try:
        return session.client("sts").get_caller_identity()["Account"]
    except Exception as e:
        print(f"Warning: Could not get account ID: {e}")
        return "Unknown"


def paginate_nat_gateways(ec2) -> list[dict]:
    paginator = ec2.get_paginator("describe_nat_gateways")
    gateways = []
    for page in paginator.paginate():
        gateways.extend(page.get("NatGateways", []))
    return gateways


def paginate_internet_gateways(ec2) -> list[dict]:
    paginator = ec2.get_paginator("describe_internet_gateways")
    gateways = []
    for page in paginator.paginate():
        gateways.extend(page.get("InternetGateways", []))
    return gateways


def get_gateway_info(gateway: dict, ec2) -> tuple[str, str | None, str | None]:
    """Return (name, vpc_id, vpc_name) for a NAT or Internet Gateway."""
    tags = gateway.get("Tags", [])
    name_tag = next((t["Value"] for t in tags if t["Key"] == "Name"), None)

    vpc_id = None
    if "VpcId" in gateway:
        vpc_id = gateway["VpcId"]
    elif "Attachments" in gateway and gateway["Attachments"]:
        vpc_id = gateway["Attachments"][0].get("VpcId")

    vpc_name = None
    if vpc_id:
        try:
            vpc = ec2.describe_vpcs(VpcIds=[vpc_id])["Vpcs"][0]
            vpc_name = next(
                (t["Value"] for t in vpc.get("Tags", []) if t["Key"] == "Name"),
                vpc_id,
            )
        except Exception:
            vpc_name = vpc_id

    if not name_tag:
        if vpc_name:
            name_tag = (
                f"IGW-{vpc_name}"
                if "InternetGatewayId" in gateway
                else f"NAT-{vpc_name}"
            )
        else:
            name_tag = gateway.get(
                "NatGatewayId", gateway.get("InternetGatewayId", "Unknown")
            )

    return name_tag, vpc_id, vpc_name


# -- Metric collection --------------------------------------------------------


def get_metric_data_chunked(
    cloudwatch,
    namespace: str,
    metric_name: str,
    dimensions: list[dict],
    start_time: datetime.datetime,
    end_time: datetime.datetime,
) -> list[dict]:
    """Fetch metric data in 30-day chunks to avoid CloudWatch limits."""
    all_datapoints: list[dict] = []
    chunk_size = datetime.timedelta(days=30)
    chunk_start = start_time

    while chunk_start < end_time:
        chunk_end = min(chunk_start + chunk_size, end_time)
        response = cloudwatch.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric_name,
            Dimensions=dimensions,
            StartTime=chunk_start,
            EndTime=chunk_end,
            Period=PERIOD_SECONDS,
            Statistics=["Sum", "Average", "Maximum", "Minimum"],
        )
        all_datapoints.extend(response["Datapoints"])
        chunk_start = chunk_end

    return all_datapoints


def collect_metrics(
    session: boto3.Session,
    region: str,
    account_id: str,
    days: int,
) -> pd.DataFrame:
    """Collect CloudWatch metrics for all NAT and Internet Gateways."""
    cloudwatch = session.client("cloudwatch")
    ec2 = session.client("ec2")

    end_time = datetime.datetime.now(timezone.utc)
    start_time = end_time - datetime.timedelta(days=days)

    base_fields = {"Account_ID": account_id, "Region": region}
    results: list[dict] = []

    # NAT Gateways
    for nat in paginate_nat_gateways(ec2):
        nat_id = nat["NatGatewayId"]
        nat_name, vpc_id, vpc_name = get_gateway_info(nat, ec2)
        print(f"Collecting metrics for NAT Gateway: {nat_name} ({nat_id})")
        print(f"  VPC: {vpc_name} ({vpc_id})")

        for metric in NAT_METRICS:
            datapoints = get_metric_data_chunked(
                cloudwatch,
                "AWS/NATGateway",
                metric,
                [{"Name": "NatGatewayId", "Value": nat_id}],
                start_time,
                end_time,
            )
            for dp in datapoints:
                results.append(
                    {
                        **base_fields,
                        "Gateway_Type": "NAT",
                        "Gateway_ID": nat_id,
                        "Gateway_Name": nat_name,
                        "VPC_ID": vpc_id,
                        "VPC_Name": vpc_name,
                        "Metric": metric,
                        "Timestamp": dp["Timestamp"],
                        "Sum": dp.get("Sum", 0),
                        "Average": dp.get("Average", 0),
                        "Maximum": dp.get("Maximum", 0),
                        "Minimum": dp.get("Minimum", 0),
                    }
                )

    # Internet Gateways
    for igw in paginate_internet_gateways(ec2):
        igw_id = igw["InternetGatewayId"]
        igw_name, vpc_id, vpc_name = get_gateway_info(igw, ec2)
        print(f"\nCollecting metrics for Internet Gateway: {igw_name} ({igw_id})")
        print(f"  VPC: {vpc_name} ({vpc_id})")

        for metric in IGW_METRICS:
            metric_exists = cloudwatch.list_metrics(
                Namespace="AWS/IGW",
                MetricName=metric,
                Dimensions=[{"Name": "InternetGatewayId", "Value": igw_id}],
            )

            if metric_exists["Metrics"]:
                print(f"  Found {metric}")
                datapoints = get_metric_data_chunked(
                    cloudwatch,
                    "AWS/IGW",
                    metric,
                    [{"Name": "InternetGatewayId", "Value": igw_id}],
                    start_time,
                    end_time,
                )
            else:
                print(f"  No data for {metric}")
                datapoints = []

            if not datapoints:
                results.append(
                    {
                        **base_fields,
                        "Gateway_Type": "IGW",
                        "Gateway_ID": igw_id,
                        "Gateway_Name": igw_name,
                        "VPC_ID": vpc_id,
                        "VPC_Name": vpc_name,
                        "Metric": metric,
                        "Timestamp": start_time,
                        "Sum": 0,
                        "Average": 0,
                        "Maximum": 0,
                        "Minimum": 0,
                    }
                )

            for dp in datapoints:
                results.append(
                    {
                        **base_fields,
                        "Gateway_Type": "IGW",
                        "Gateway_ID": igw_id,
                        "Gateway_Name": igw_name,
                        "VPC_ID": vpc_id,
                        "VPC_Name": vpc_name,
                        "Metric": metric,
                        "Timestamp": dp["Timestamp"],
                        "Sum": dp.get("Sum", 0),
                        "Average": dp.get("Average", 0),
                        "Maximum": dp.get("Maximum", 0),
                        "Minimum": dp.get("Minimum", 0),
                    }
                )

    return pd.DataFrame(results)


# -- Analysis ------------------------------------------------------------------


def analyze_idle_time(
    df: pd.DataFrame, account_id: str, region: str
) -> pd.DataFrame:
    """Compute idle percentage and traffic summaries per gateway."""
    idle_analysis: list[dict] = []

    for gateway_type, metrics_list in [("NAT", NAT_METRICS), ("IGW", IGW_METRICS)]:
        traffic_metrics = [m for m in metrics_list if "Bytes" in m or "Packets" in m]
        gateways = df[df["Gateway_Type"] == gateway_type]["Gateway_ID"].unique()

        for gateway_id in gateways:
            gw = df[
                (df["Gateway_Type"] == gateway_type) & (df["Gateway_ID"] == gateway_id)
            ]
            row0 = gw.iloc[0]

            total_periods = len(gw["Timestamp"].unique())
            idle_periods = len(
                gw[
                    (gw["Metric"].isin(traffic_metrics)) & (gw["Sum"] == 0)
                ]["Timestamp"].unique()
            )
            idle_pct = (idle_periods / total_periods * 100) if total_periods > 0 else 0

            summary: dict = {
                "Account_ID": account_id,
                "Region": region,
                "VPC_ID": row0.get("VPC_ID", "Unknown"),
                "VPC_Name": row0.get("VPC_Name", "Unknown"),
                "Gateway_Type": gateway_type,
                "Gateway_ID": gateway_id,
                "Gateway_Name": row0["Gateway_Name"],
                "Total_Periods": total_periods,
                "Idle_Periods": idle_periods,
                "Idle_Percentage": round(idle_pct, 2),
            }

            if gateway_type == "NAT":
                summary.update(_nat_summary(gw))
            else:
                summary.update(_igw_summary(gw))

            total_bytes = summary.get("Total_Bytes_In", 0) + summary.get("Total_Bytes_Out", 0)
            total_packets = summary.get("Total_Packets_In", 0) + summary.get("Total_Packets_Out", 0)
            secs = total_periods * PERIOD_SECONDS if total_periods > 0 else 1
            summary.update(
                {
                    "Total_Bytes": total_bytes,
                    "Total_Packets": total_packets,
                    "Bytes_Per_Second_Avg": round(total_bytes / secs, 2),
                    "Packets_Per_Second_Avg": round(total_packets / secs, 2),
                }
            )

            idle_analysis.append(summary)

    return pd.DataFrame(idle_analysis)


def _nat_summary(gw: pd.DataFrame) -> dict:
    return {
        "Total_Bytes_In": gw[
            gw["Metric"].isin(["BytesInFromSource", "BytesInFromDestination"])
        ]["Sum"].sum(),
        "Total_Bytes_Out": gw[
            gw["Metric"].isin(["BytesOutToSource", "BytesOutToDestination"])
        ]["Sum"].sum(),
        "Total_Packets_In": gw[
            gw["Metric"].isin(["PacketsInFromSource", "PacketsInFromDestination"])
        ]["Sum"].sum(),
        "Total_Packets_Out": gw[
            gw["Metric"].isin(["PacketsOutToSource", "PacketsOutToDestination"])
        ]["Sum"].sum(),
        "Total_Connection_Attempts": gw[gw["Metric"] == "ConnectionAttemptCount"][
            "Sum"
        ].sum(),
        "Total_Connection_Timeouts": gw[gw["Metric"] == "IdleTimeoutCount"][
            "Sum"
        ].sum(),
        "Port_Allocation_Errors": gw[gw["Metric"] == "ErrorPortAllocation"][
            "Sum"
        ].sum(),
        "Max_Active_Connections": (
            gw[gw["Metric"] == "ActiveConnectionCount"]["Maximum"].max()
            if "ActiveConnectionCount" in gw["Metric"].values
            else 0
        ),
        "Avg_Active_Connections": (
            gw[gw["Metric"] == "ActiveConnectionCount"]["Average"].mean()
            if "ActiveConnectionCount" in gw["Metric"].values
            else 0
        ),
    }


def _igw_summary(gw: pd.DataFrame) -> dict:
    return {
        "Total_Bytes_In": gw[gw["Metric"] == "BytesInFromDestination"]["Sum"].sum(),
        "Total_Bytes_Out": gw[gw["Metric"] == "BytesOutToDestination"]["Sum"].sum(),
        "Total_Packets_In": gw[gw["Metric"] == "PacketsInFromDestination"][
            "Sum"
        ].sum(),
        "Total_Packets_Out": gw[gw["Metric"] == "PacketsOutToDestination"][
            "Sum"
        ].sum(),
        "Total_Blackhole_Drops_Bytes": gw[
            gw["Metric"] == "BytesDropCountBlackholeIPv4"
        ]["Sum"].sum(),
        "Total_NoRoute_Drops_Bytes": gw[gw["Metric"] == "BytesDropCountNoRouteIPv4"][
            "Sum"
        ].sum(),
        "Total_Blackhole_Drops_Packets": gw[
            gw["Metric"] == "PacketsDropCountBlackholeIPv4"
        ]["Sum"].sum(),
        "Total_NoRoute_Drops_Packets": gw[
            gw["Metric"] == "PacketsDropCountNoRouteIPv4"
        ]["Sum"].sum(),
        "Status": "Inactive" if all(gw["Sum"] == 0) else "Active",
    }


# -- Export --------------------------------------------------------------------


def export_excel(
    df: pd.DataFrame,
    idle_analysis: pd.DataFrame,
    output_path: Path,
) -> None:
    """Write results to an Excel file with summary + per-gateway sheets."""
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        # Summary sheet
        summary = idle_analysis.copy()
        for col in summary.select_dtypes(include=["datetime64[ns, UTC]"]).columns:
            summary[col] = summary[col].dt.tz_localize(None)
        summary.to_excel(writer, sheet_name="Summary", index=False)

        # Per-gateway sheets
        for gateway_id in df["Gateway_ID"].unique():
            gw_data = df[df["Gateway_ID"] == gateway_id].copy()
            gw_data["Timestamp"] = gw_data["Timestamp"].dt.tz_localize(None)
            sheet_name = gw_data.iloc[0]["Gateway_Name"][:31]
            gw_data.to_excel(writer, sheet_name=sheet_name, index=False)

        # Auto-adjust column widths
        for sheet_name in writer.sheets:
            ws = writer.sheets[sheet_name]
            for idx, col in enumerate(ws.columns, 1):
                max_len = max(
                    (len(str(cell.value)) for cell in col if cell.value is not None),
                    default=0,
                )
                ws.column_dimensions[get_column_letter(idx)].width = min(max_len + 2, 50)


def print_summary(idle_analysis: pd.DataFrame, account_id: str, region: str) -> None:
    """Print a human-readable summary to stdout."""
    print(f"\nGateway Analysis Summary")
    print("=" * 80)
    print(f"Account: {account_id}  |  Region: {region}")
    print("=" * 80)

    for _, row in idle_analysis.iterrows():
        print(f"\n{row['Gateway_Type']} Gateway: {row['Gateway_Name']} ({row['Gateway_ID']})")
        print(f"  VPC: {row['VPC_Name']} ({row['VPC_ID']})")
        print(f"  Idle: {row['Idle_Percentage']}%")
        print(f"  Traffic: {row['Total_Bytes_In']:,} bytes in / {row['Total_Bytes_Out']:,} bytes out")
        print(f"  Packets: {row['Total_Packets_In']:,} in / {row['Total_Packets_Out']:,} out")
        print(f"  Avg rates: {row['Bytes_Per_Second_Avg']:,.2f} B/s, {row['Packets_Per_Second_Avg']:,.2f} pkt/s")

        if row["Gateway_Type"] == "NAT":
            print(f"  Connections: {row['Total_Connection_Attempts']:,} attempts, "
                  f"{row['Total_Connection_Timeouts']:,} timeouts, "
                  f"{row['Port_Allocation_Errors']:,} port errors")
            print(f"  Active connections: max {row['Max_Active_Connections']:,}, "
                  f"avg {row['Avg_Active_Connections']:,.2f}")
        else:
            print(f"  Status: {row['Status']}")
            print(f"  Drops: {row['Total_Blackhole_Drops_Bytes']:,} blackhole bytes, "
                  f"{row['Total_NoRoute_Drops_Bytes']:,} no-route bytes")


# -- CLI -----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="idle_gw",
        description="Detect idle NAT Gateways and Internet Gateways using CloudWatch metrics.",
    )
    parser.add_argument("--profile", default=None, help="AWS CLI profile name")
    parser.add_argument("--region", required=True, help="AWS region (e.g. us-east-1)")
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Number of days to look back (default: 90)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output Excel file path (default: gateway_analysis_<account>_<region>_<timestamp>.xlsx)",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    session = make_session(args.profile, args.region)
    account_id = get_account_id(session)
    print(f"Analyzing gateways for Account: {account_id}, Region: {args.region}")

    df = collect_metrics(session, args.region, account_id, args.days)

    if df.empty:
        print("No metrics data found for the specified period.")
        return

    idle_analysis = analyze_idle_time(df, account_id, args.region)

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = Path(f"gateway_analysis_{account_id}_{args.region}_{ts}.xlsx")

    export_excel(df, idle_analysis, output_path)
    print_summary(idle_analysis, account_id, args.region)
    print(f"\nDetailed analysis saved to: {output_path}")


if __name__ == "__main__":
    main()
