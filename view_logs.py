"""
view_logs.py
Pretty-prints the incident log in the terminal.
Usage: python view_logs.py [--last N] [--severity red|yellow]
"""

import argparse
import json
from pathlib import Path

LOG_FILE = Path(__file__).parent / "logs" / "incidents.jsonl"

ICONS = {"red": "🔴", "yellow": "🟡"}
COLORS = {"red": "\033[91m", "yellow": "\033[93m", "reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m"}


def parse_args():
    p = argparse.ArgumentParser(description="View AI incident logs")
    p.add_argument("--last", type=int, default=20, help="Show last N incidents (default: 20)")
    p.add_argument("--severity", choices=["red", "yellow"], help="Filter by severity")
    return p.parse_args()


def main():
    args = parse_args()

    if not LOG_FILE.exists():
        print("No incidents logged yet.")
        return

    with open(LOG_FILE) as f:
        entries = [json.loads(line) for line in f if line.strip()]

    if args.severity:
        entries = [e for e in entries if e["severity"] == args.severity]

    entries = entries[-args.last:]

    if not entries:
        print("No matching incidents found.")
        return

    print(f"\n{COLORS['bold']}{'─' * 60}{COLORS['reset']}")
    print(f"{COLORS['bold']}  AI Incident Log  ({len(entries)} entries){COLORS['reset']}")
    print(f"{COLORS['bold']}{'─' * 60}{COLORS['reset']}\n")

    for e in entries:
        icon = ICONS.get(e["severity"], "⚪")
        color = COLORS.get(e["severity"], "")
        notified = "✓ sent" if e.get("notified") else "✗ failed"

        print(f"{color}{COLORS['bold']}{icon} {e['severity'].upper()} — {e['metric']}{COLORS['reset']}")
        print(f"  {COLORS['dim']}Time:      {e['timestamp']}{COLORS['reset']}")
        print(f"  {COLORS['dim']}Value:     {e['value']}% (threshold: {e['threshold']}%){COLORS['reset']}")
        print(f"  {COLORS['dim']}Telegram:  {notified}{COLORS['reset']}")
        print()

        for line in e.get("summary", "").splitlines():
            label, _, content = line.partition(": ")
            print(f"  {COLORS['bold']}{label}:{COLORS['reset']} {content}")

        print(f"\n{'─' * 60}\n")


if __name__ == "__main__":
    main()
