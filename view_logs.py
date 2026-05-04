"""
view_logs.py
Pretty-prints the incident log in the terminal.

v2: reads from SQLite via db.py

Usage:
  python view_logs.py                        # last 20 active incidents
  python view_logs.py --last 10              # last 10 active
  python view_logs.py --severity red         # filter by severity
  python view_logs.py --archive              # show resolved/archived incidents
"""

import argparse

import db

ICONS  = {"red": "[RED]", "yellow": "[YELLOW]"}
COLORS = {
    "red":    "\033[91m",
    "yellow": "\033[93m",
    "reset":  "\033[0m",
    "dim":    "\033[2m",
    "bold":   "\033[1m",
}


def parse_args():
    p = argparse.ArgumentParser(description="View incident logs")
    p.add_argument("--last",     type=int, default=20, help="Show last N incidents (default: 20)")
    p.add_argument("--severity", choices=["red", "yellow"], help="Filter by severity")
    p.add_argument("--archive",  action="store_true", help="Show resolved/archived incidents")
    return p.parse_args()


def print_incident(e: dict, archived: bool = False):
    icon    = ICONS.get(e["severity"], "[?]")
    color   = COLORS.get(e["severity"], "")
    sent    = "sent"  if e.get("notified")     else "failed"
    ack     = "acked" if e.get("acknowledged") else "—"

    label = "ARCHIVED" if archived else e["severity"].upper()
    print(f"{color}{COLORS['bold']}{icon} {label} — {e['metric']}{COLORS['reset']}")
    print(f"  {COLORS['dim']}Time:      {e['timestamp']}{COLORS['reset']}")
    print(f"  {COLORS['dim']}Value:     {e['value']}% (threshold: {e['threshold']}%){COLORS['reset']}")
    print(f"  {COLORS['dim']}Telegram:  {sent}{COLORS['reset']}")
    print(f"  {COLORS['dim']}ACK:       {ack}{COLORS['reset']}")

    if archived and e.get("resolved_at"):
        print(f"  {COLORS['dim']}Resolved:  {e['resolved_at']}{COLORS['reset']}")

    print()
    for line in (e.get("summary") or "").splitlines():
        label_part, _, content = line.partition(": ")
        print(f"  {COLORS['bold']}{label_part}:{COLORS['reset']} {content}")

    print(f"\n{'─' * 60}\n")


def main():
    args = parse_args()
    db.init_db()

    if args.archive:
        total, entries = db.get_archive(limit=args.last, offset=0)
        section = "Resolved / Archive"
        archived = True
    else:
        total, entries = db.get_incidents(
            severity=args.severity,
            limit=args.last,
            offset=0,
        )
        # get_incidents returns newest first; reverse for chronological CLI display
        entries = list(reversed(entries))
        section = "Active Incidents"
        archived = False

    if not entries:
        print("No matching incidents found.")
        return

    print(f"\n{COLORS['bold']}{'─' * 60}{COLORS['reset']}")
    print(f"{COLORS['bold']}  {section}  ({len(entries)} of {total}){COLORS['reset']}")
    print(f"{COLORS['bold']}{'─' * 60}{COLORS['reset']}\n")

    for e in entries:
        print_incident(e, archived=archived)


if __name__ == "__main__":
    main()
