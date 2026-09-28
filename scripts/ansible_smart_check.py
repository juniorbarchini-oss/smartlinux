#!/usr/bin/env python3
"""
ansible_smart_check.py - Standalone S.M.A.R.T. Health Check for Ansible.

Extracts the classification and threshold logic from SmartLinux (smart_parser.py)
into a zero-dependency script designed to run directly on Homelab servers (e.g. i7server)
without PySide6, paramiko, or any GUI modules.

CLI Usage:
    python3 ansible_smart_check.py /dev/sdX
    python3 ansible_smart_check.py /dev/nvme0n1
    python3 ansible_smart_check.py --device /dev/sdc --webhook-url https://discord.com/api/webhooks/...

Exit Codes (for Ansible tasks / playbooks):
    0 = HEALTHY (All parameters normal)
    1 = WARNING (Pending/reallocated sectors, wear >= 95%, spare <= threshold, etc.)
    2 = FAILED  (Critical trip, uncorrectable errors, missing drive, or read error)

Discord Webhook (Item 5.4):
    If the result is WARNING or FAILED, sends a rich embed to DISCORD_WEBHOOK_URL
    using standard library urllib.request (zero third-party dependencies).
    If HEALTHY, no notification is sent.
"""

import argparse
import datetime
import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

EXIT_HEALTHY = 0
EXIT_WARNING = 1
EXIT_FAILED = 2

# Critical ATA SMART attribute IDs
CRITICAL_ATA_ATTRS = {
    5: "Reallocated Sectors (Bad Sectors)",
    187: "Reported Uncorrectable Errors",
    188: "Command Timeouts",
    196: "Reallocation Event Count",
    197: "Current Pending Sector Count",
    198: "Offline Uncorrectable Sector Count",
    199: "UDMA CRC Error Count (Potential cable/interface issue)"
}


def format_bytes_commercial(size_bytes: int) -> str:
    """Formats bytes into human readable commercial decimal units (GB/TB)."""
    if size_bytes <= 0:
        return "Unknown"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(size_bytes)
    unit_idx = 0
    while size >= 1000.0 and unit_idx < len(units) - 1:
        size /= 1000.0
        unit_idx += 1
    if unit_idx >= 3:
        return f"{size:.1f} {units[unit_idx]}" if size < 100 else f"{int(round(size))} {units[unit_idx]}"
    return f"{size:.0f} {units[unit_idx]}"


def find_smartctl_binary() -> Optional[str]:
    """Finds the smartctl executable in PATH or standard system locations."""
    candidates = [
        shutil.which("smartctl"),
        "/usr/sbin/smartctl",
        "/usr/bin/smartctl",
        "/sbin/smartctl",
        "/usr/local/sbin/smartctl",
        "/usr/local/bin/smartctl",
        "/opt/homebrew/bin/smartctl",
        "/opt/homebrew/sbin/smartctl",
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def run_smartctl(device_path: str, device_type: Optional[str] = None) -> Tuple[int, Optional[Dict[str, Any]], str]:
    """
    Executes smartctl -j -a against the given device path with bridge fallback.
    Returns (exit_code, parsed_json, error_message).
    """
    smartctl_bin = find_smartctl_binary()
    if not smartctl_bin:
        return EXIT_FAILED, None, "smartctl binary not found in system PATH or standard directories."

    # If device type is explicitly provided, run once with it
    if device_type:
        types_to_try = [device_type]
    else:
        types_to_try = [None, "scsi", "sat,auto", "sntrealtek", "sntjmicron", "sntasmedia", "usbjmicron", "usbsunplus"]

    last_error = ""
    for dev_type in types_to_try:
        cmd = [smartctl_bin, "-j"]
        if dev_type:
            cmd.extend(["-d", dev_type])
        cmd.extend(["-a", device_path])

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if proc.stdout:
                try:
                    data = json.loads(proc.stdout)
                    # Check if smartctl returned usable data
                    has_smart = (
                        "smart_status" in data or
                        "ata_smart_attributes" in data or
                        "nvme_smart_health_information_log" in data
                    )
                    has_identity = (
                        "model_name" in data or
                        "scsi_product" in data or
                        "user_capacity" in data or
                        "serial_number" in data
                    )
                    if has_smart or has_identity:
                        return proc.returncode, data, ""
                except json.JSONDecodeError:
                    pass

            if proc.stderr:
                last_error = proc.stderr.strip()
        except subprocess.TimeoutExpired:
            last_error = f"smartctl timed out while reading {device_path}"
        except Exception as e:
            last_error = str(e)

    return EXIT_FAILED, None, last_error or "Unable to read S.M.A.R.T. data from device."


def evaluate_smart_data(data: Dict[str, Any], device_path: str) -> Dict[str, Any]:
    """
    Evaluates S.M.A.R.T. JSON using SmartLinux classification logic.
    Returns a dictionary with status, exit code, summary, reasons, and device metadata.
    """
    model = (
        data.get("model_name") or
        data.get("scsi_model_name") or
        data.get("device", {}).get("model_name") or
        (f"{data.get('scsi_vendor', '')} {data.get('scsi_product', '')}").strip() or
        data.get("model_family") or
        "Generic Drive"
    ).strip()

    serial = (data.get("serial_number") or data.get("scsi_serial_number") or "N/A").strip()
    user_cap = data.get("user_capacity", {})
    size_bytes = int(user_cap.get("bytes", 0))
    size_human = format_bytes_commercial(size_bytes)

    # Protocol
    dev_protocol = data.get("device", {}).get("protocol", "").upper()
    dev_type = data.get("device", {}).get("type", "").upper()
    if "NVME" in dev_protocol or "NVME" in dev_type or "nvme" in device_path.lower():
        protocol = "NVMe"
    elif "USB" in dev_protocol or "USB" in dev_type:
        protocol = "USB"
    elif "SATA" in dev_protocol or "ATA" in dev_protocol:
        protocol = "SATA"
    elif "SCSI" in dev_protocol or "SAS" in dev_protocol:
        protocol = "SCSI/SAS"
    else:
        protocol = dev_protocol or "ATA/SATA"

    # Telemetry
    temp_dict = data.get("temperature", {})
    temp_c = int(temp_dict.get("current", 0)) if int(temp_dict.get("current", 0)) > 0 else None
    power_hours = data.get("power_on_time", {}).get("hours")

    smart_status = data.get("smart_status", {})
    passed = smart_status.get("passed", True)

    reasons: List[str] = []
    health_status = "HEALTHY"
    exit_code = EXIT_HEALTHY
    summary = "Healthy (All parameters within normal thresholds)"

    # NVMe evaluation
    if "nvme_smart_health_information_log" in data:
        nvme_log = data.get("nvme_smart_health_information_log", {})
        crit_warning = nvme_log.get("critical_warning", 0)
        avail_spare = nvme_log.get("available_spare", 100)
        avail_spare_thresh = nvme_log.get("available_spare_threshold", 10)
        pct_used = nvme_log.get("percentage_used", 0)
        media_errors = nvme_log.get("media_errors", 0)

        if temp_c is None and nvme_log.get("temperature"):
            temp_c = int(nvme_log.get("temperature", 0))
        if power_hours is None and nvme_log.get("power_on_hours"):
            power_hours = int(nvme_log.get("power_on_hours", 0))

        if not passed:
            reasons.append("Overall SMART assessment: FAILED")
        if crit_warning > 0:
            reasons.append(f"Critical warning bitmask: 0x{crit_warning:02X}")
        if media_errors > 0:
            reasons.append(f"Media and data integrity errors: {media_errors}")

        if not passed or crit_warning > 0 or media_errors > 0:
            health_status = "FAILED"
            exit_code = EXIT_FAILED
            summary = "Critical Failure / Integrity Errors detected"
        elif avail_spare <= avail_spare_thresh or pct_used >= 95:
            health_status = "WARNING"
            exit_code = EXIT_WARNING
            if avail_spare <= avail_spare_thresh:
                reasons.append(f"Available spare ({avail_spare}%) <= threshold ({avail_spare_thresh}%)")
            if pct_used >= 95:
                reasons.append(f"High endurance wear ({pct_used}%)")
            summary = f"Warning: High wear ({pct_used}%) or Low Spare ({avail_spare}%)"
        else:
            health_status = "HEALTHY"
            exit_code = EXIT_HEALTHY
            summary = "Healthy (NVMe Health Passed)"

    # ATA evaluation
    elif "ata_smart_attributes" in data:
        attr_table = data.get("ata_smart_attributes", {}).get("table", [])
        has_critical_failure = not passed
        has_warning = False

        if not passed:
            reasons.append("Overall SMART status: FAILED")

        for item in attr_table:
            attr_id = int(item.get("id", 0))
            name = item.get("name", f"Attr_{attr_id}")
            val = item.get("value", 0)
            thresh = item.get("thresh", 0)
            raw_dict = item.get("raw", {})
            raw_val = raw_dict.get("value", 0)
            raw_str = raw_dict.get("string", str(raw_val))
            when_failed = item.get("when_failed", "")

            # Temperature fallback from attr 194 / 190
            if attr_id in (194, 190) and temp_c is None:
                try:
                    temp_c = int(str(raw_str).split()[0])
                except (ValueError, IndexError):
                    pass

            # Critical failure: threshold breached or failing
            if (thresh > 0 and val <= thresh) or when_failed in ("FAILING_NOW", "In_the_past"):
                has_critical_failure = True
                reasons.append(f"FAILED: {name} (Value: {val} <= Thresh: {thresh}) [{when_failed or 'BREACHED'}]")
            # Warning: pending/reallocated sectors
            elif attr_id in (5, 196, 197, 198) and raw_val > 0:
                has_warning = True
                reasons.append(f"{name}: {raw_str} (ID {attr_id})")
            # Warning: uncorrectable / timeouts
            elif attr_id in (187, 188) and raw_val > 0:
                has_warning = True
                reasons.append(f"{name}: {raw_str} (ID {attr_id})")

        if has_critical_failure:
            health_status = "FAILED"
            exit_code = EXIT_FAILED
            summary = "Imminent Failure / Threshold Breached"
        elif has_warning:
            health_status = "WARNING"
            exit_code = EXIT_WARNING
            summary = f"Warning: {', '.join(reasons[:2])}"
        elif passed:
            health_status = "HEALTHY"
            exit_code = EXIT_HEALTHY
            summary = "Healthy (All parameters within normal thresholds)"
        else:
            health_status = "FAILED"
            exit_code = EXIT_FAILED
            summary = "SMART self-test reported failure"

    # Generic / SCSI fallback
    else:
        if passed is True:
            health_status = "HEALTHY"
            exit_code = EXIT_HEALTHY
            summary = "Healthy (SMART Passed)"
        elif passed is False:
            health_status = "FAILED"
            exit_code = EXIT_FAILED
            summary = "Critical Failure Detected (SMART Failed)"
            reasons.append("Overall SMART assessment failed")
        else:
            health_status = "FAILED"
            exit_code = EXIT_FAILED
            summary = "SMART telemetry not available"
            reasons.append("Device does not return valid SMART status")

    return {
        "device": device_path,
        "model": model,
        "serial": serial,
        "protocol": protocol,
        "size_human": size_human,
        "size_bytes": size_bytes,
        "temperature_c": temp_c,
        "power_on_hours": power_hours,
        "health_status": health_status,
        "exit_code": exit_code,
        "summary": summary,
        "reasons": reasons,
    }


def send_discord_alert(webhook_url: str, result: Dict[str, Any], host: str) -> Tuple[bool, str]:
    """
    Sends a rich Discord embed alert for WARNING or FAILED status via urllib.request.
    Zero external dependencies (does not require requests).
    """
    if not webhook_url:
        return False, "DISCORD_WEBHOOK_URL is not configured."

    is_failed = result["exit_code"] == EXIT_FAILED
    color = 0xEF4444 if is_failed else 0xF59E0B  # Red or Amber
    status_icon = "🔴🚨" if is_failed else "⚠️"
    status_text = "CRITICAL FAILURE" if is_failed else "WARNING"
    title = f"{status_icon} S.M.A.R.T. {status_text}: {host} ({result['device']})"

    fields = [
        {"name": "Host", "value": f"`{host}`", "inline": True},
        {"name": "Device", "value": f"`{result['device']}`", "inline": True},
        {"name": "Status", "value": f"**{result['health_status']}**", "inline": True},
        {"name": "Model", "value": result["model"], "inline": True},
        {"name": "Serial", "value": f"`{result['serial']}`", "inline": True},
        {"name": "Protocol", "value": f"{result['protocol']} ({result['size_human']})", "inline": True},
    ]

    telemetry_parts = []
    if result["temperature_c"] is not None:
        telemetry_parts.append(f"{result['temperature_c']} °C")
    if result["power_on_hours"] is not None:
        telemetry_parts.append(f"{result['power_on_hours']:,} hrs")
    if telemetry_parts:
        fields.append({"name": "Telemetry", "value": " | ".join(telemetry_parts), "inline": True})

    if result["reasons"]:
        anomalies = "\n".join(f"• {r}" for r in result["reasons"][:10])
        fields.append({"name": "Anomalies Detected", "value": anomalies, "inline": False})
    else:
        fields.append({"name": "Summary", "value": result["summary"], "inline": False})

    payload = {
        "username": "SmartLinux Ansible Monitor",
        "embeds": [
            {
                "title": title,
                "description": f"Automated S.M.A.R.T. health check detected disk issues on **{host}**.",
                "color": color,
                "fields": fields,
                "footer": {"text": "SmartLinux Ansible Storage Diagnostic"},
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }
        ]
    }

    try:
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=req_data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "SmartLinux-Ansible/1.0"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 204):
                return True, "Discord notification sent successfully."
            return False, f"Discord webhook returned unexpected HTTP {resp.status}."
    except urllib.error.HTTPError as e:
        return False, f"Discord webhook HTTP error: {e.code} - {e.reason}"
    except urllib.error.URLError as e:
        return False, f"Discord webhook connection error: {e.reason}"
    except Exception as e:
        return False, f"Discord webhook failed: {str(e)}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Standalone S.M.A.R.T. Health Check for Ansible with Discord alert notifications."
    )
    parser.add_argument(
        "device",
        nargs="?",
        default=None,
        help="Target block device path (e.g., /dev/sda, /dev/nvme0n1)"
    )
    parser.add_argument(
        "--device",
        "-d",
        dest="opt_device",
        default=None,
        help="Explicit device path (alternative to positional argument)"
    )
    parser.add_argument(
        "--type",
        "-t",
        dest="device_type",
        default=None,
        help="Optional smartctl device type override (e.g. sat, scsi, nvme)"
    )
    parser.add_argument(
        "--webhook-url",
        dest="webhook_url",
        default=None,
        help="Discord Webhook URL for alerts (defaults to DISCORD_WEBHOOK_URL env var)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output structured JSON summary instead of text"
    )
    parser.add_argument(
        "--json-file",
        dest="json_file",
        default=None,
        help="Evaluate S.M.A.R.T. JSON from file instead of executing smartctl (for tests/playbook pipelines)"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    device_path = args.device or args.opt_device

    if not device_path and not args.json_file:
        sys.stderr.write("Error: device path is required (e.g. python3 ansible_smart_check.py /dev/sda)\n")
        return EXIT_FAILED

    hostname = socket.gethostname()

    # Load S.M.A.R.T. data
    if args.json_file:
        device_path = device_path or args.json_file
        try:
            with open(args.json_file, "r", encoding="utf-8") as f:
                smart_data = json.load(f)
        except Exception as e:
            sys.stderr.write(f"Error reading JSON file '{args.json_file}': {e}\n")
            return EXIT_FAILED
    else:
        _, smart_data, err_msg = run_smartctl(device_path, args.device_type)
        if not smart_data:
            sys.stderr.write(f"Error checking {device_path}: {err_msg}\n")
            # Attempt to send critical failure notification to Discord if configured
            webhook_url = args.webhook_url or os.environ.get("DISCORD_WEBHOOK_URL")
            if webhook_url:
                failure_result = {
                    "device": device_path,
                    "model": "Unknown",
                    "serial": "Unknown",
                    "protocol": "Unknown",
                    "size_human": "Unknown",
                    "temperature_c": None,
                    "power_on_hours": None,
                    "health_status": "FAILED",
                    "exit_code": EXIT_FAILED,
                    "summary": f"Failed to execute smartctl: {err_msg}",
                    "reasons": [err_msg]
                }
                send_discord_alert(webhook_url, failure_result, hostname)
            return EXIT_FAILED

    # Evaluate health
    result = evaluate_smart_data(smart_data, device_path)

    # Discord alert (5.4) for WARNING or FAILED
    discord_status = "Not applicable (HEALTHY)"
    if result["exit_code"] in (EXIT_WARNING, EXIT_FAILED):
        webhook_url = args.webhook_url or os.environ.get("DISCORD_WEBHOOK_URL")
        if webhook_url:
            ok, msg = send_discord_alert(webhook_url, result, hostname)
            discord_status = msg if ok else f"Error: {msg}"
        else:
            discord_status = "Skipped (DISCORD_WEBHOOK_URL environment variable not set)"
            sys.stderr.write("[Notice] DISCORD_WEBHOOK_URL not configured; skipping Discord alert.\n")

    result["discord_notification"] = discord_status
    result["host"] = hostname

    # Output formatting
    if args.json_output:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        status_label = result["health_status"]
        border = "=" * 62
        print(border)
        print(f"S.M.A.R.T. Health Check: {result['device']} [{status_label}]")
        print(border)
        print(f"Host:        {hostname}")
        print(f"Device:      {result['device']}")
        print(f"Model:       {result['model']}")
        print(f"Serial:      {result['serial']}")
        print(f"Protocol:    {result['protocol']}")
        print(f"Capacity:    {result['size_human']}")
        if result["temperature_c"] is not None:
            print(f"Temperature: {result['temperature_c']} °C")
        if result["power_on_hours"] is not None:
            print(f"Power-On:    {result['power_on_hours']:,} hrs")
        print(f"Status:      {status_label} (Exit Code: {result['exit_code']})")
        print(f"Summary:     {result['summary']}")
        if result["reasons"]:
            print("Anomalies:")
            for r in result["reasons"]:
                print(f"  • {r}")
        if result["exit_code"] != EXIT_HEALTHY:
            print(f"Discord:     {discord_status}")
        print(border)

    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
