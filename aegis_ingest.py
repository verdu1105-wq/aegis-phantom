"""
aegis_ingest.py -- AEGIS PHANTOM // Evidence Ingestion + Human-Decision Export
Cybergrid Solutions LLC

=============================================================================
WHY THIS EXISTS
=============================================================================
1. INGESTION: airlock's evidence was being POSTed to the (now dead) Cloud Run
   service and silently discarded on failure (try/except: pass). Every JOIN,
   CWIS kill, and BLOCK FAILURE has been vanishing since the billing freeze.
   This module writes every event to a LOCAL JSONL file airlock owns, so
   nothing is ever silently dropped again. This is the foundation of the whole
   RECON/Oracle/cross-reference pipeline -- it can't analyze data it never kept.

2. HUMAN-DECISION EXPORT: produces a CSV/report where the AI GIFT-WRAPS the
   evidence but the HUMAN (Jess, the host) DELIVERS the decision. Every row
   carries the grounded evidence AND an empty DECISION column for the human to
   fill. The system never marks anyone "goon" as final truth -- it presents
   what was OBSERVED, flags what is SUGGESTED, and leaves the verdict to the
   accountable human. That human-in-the-loop design is the governance safeguard
   (no "the AI blocked me" -- a person decided, with an audit trail) AND the
   enterprise/gov selling point.

GROUNDING (same discipline as everywhere):
   OBSERVED   : join happened, block failed, appeared N times  -> facts
   SUGGESTED  : bot-ish handle, numeric suffix, EE-signature    -> weak flags
   RECOGNITION: "Jess recognizes as X"                          -> human input col
   DECISION   : BLOCK / SAFE / WATCH                            -> HUMAN fills this
   The three are kept in SEPARATE columns. The system never collapses a
   suggestion into a verdict.
=============================================================================
"""

import os
import csv
import json
from datetime import datetime, timezone
from collections import defaultdict

# --- where the local evidence lives (airlock owns this) --------------------
INGEST_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "evidence"
)
os.makedirs(INGEST_DIR, exist_ok=True)

# one JSONL per day; append-only, line-buffered so a crash can't lose data
def _today_path():
    day = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(INGEST_DIR, f"evidence_{day}.jsonl")


def _utc():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# INGESTION -- call these from airlock. Every call is wrapped so an ingestion
# bug can NEVER take down the defense engine (worst case: a line isn't logged).
# ---------------------------------------------------------------------------
def _append(record: dict):
    try:
        record.setdefault("ts", _utc())
        with open(_today_path(), "a", encoding="utf-8", buffering=1) as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        # last resort -- never raise into the engine
        print(f"[INGEST] append failed (non-fatal): {e}")


def log_join(username: str, target: str, goon: bool = False,
             gator: bool = False, ee_flag: bool = False,
             ghost_lock_active: bool = False):
    """Call from on_join. Records that an account joined -- OBSERVED fact."""
    _append({
        "event": "JOIN",
        "username": (username or "").lstrip("@").lower(),
        "target": target,
        # OBSERVED engine flags at join time (these are the engine's existing
        # detections, not new inference):
        "goon_vault_hit": bool(goon),
        "gator_hit": bool(gator),
        "ee_signature": bool(ee_flag),
        "joined_during_ghost_lock": bool(ghost_lock_active),
    })


def log_kill(username: str, reason: str, kill_number: int, target: str = ""):
    """Call when a CWIS kill is registered (block attempted)."""
    _append({
        "event": "CWIS_KILL",
        "username": (username or "").lstrip("@").lower(),
        "reason": reason,
        "kill_number": kill_number,
        "target": target,
    })


def log_block_result(username: str, success: bool, method: str = "",
                     detail: str = "", target: str = ""):
    """
    Call when a block resolves -- SUCCESS or FAILURE.
    THIS IS THE KEY SIGNAL in Vern's architecture: a block that FAILS on a
    joiner is a strong 'true vampire' indicator worth enriching. Now it's
    captured instead of discarded.
    """
    _append({
        "event": "BLOCK_RESULT",
        "username": (username or "").lstrip("@").lower(),
        "block_success": bool(success),
        "method": method,          # e.g. "playwright" / "api_fallback"
        "detail": detail,
        "target": target,
    })


def log_recognition(username: str, recognized_as: str, basis: str,
                    by: str = "host"):
    """
    Human recognition input (e.g. Jess says 'this is IBPrincess'). Stored
    SEPARATELY and graded as recognition -- never promoted to observed fact.
    """
    _append({
        "event": "RECOGNITION",
        "username": (username or "").lstrip("@").lower(),
        "recognized_as": recognized_as,
        "basis": basis,             # WHY the host recognizes them
        "by": by,
        "grade": "human_recognition_unverified",
    })


def log_decision(username: str, decision: str, by: str = "host",
                 note: str = ""):
    """
    The HUMAN's final call: BLOCK / SAFE / WATCH. This is the audit trail --
    'a person decided X at time T'. This is what makes the platform defensible:
    the AI surfaced evidence; the accountable human delivered the verdict.
    """
    _append({
        "event": "DECISION",
        "username": (username or "").lstrip("@").lower(),
        "decision": decision.upper(),   # BLOCK | SAFE | WATCH
        "by": by,
        "note": note,
    })


# ---------------------------------------------------------------------------
# EXPORT -- build the human-decision report from the day's evidence.
# Aggregates per-account, presents OBSERVED + SUGGESTED, leaves DECISION blank
# for the host to fill. Outputs CSV (portable -> Excel, Discord, anywhere).
# ---------------------------------------------------------------------------
def build_report(date: str = None, out_path: str = None) -> str:
    """
    date: 'YYYY-MM-DD' (defaults to today).
    Produces a CSV where each row is one account with its grounded evidence
    and an empty DECISION column for the human.
    Returns the CSV path.
    """
    day = date or datetime.now().strftime("%Y-%m-%d")
    src = os.path.join(INGEST_DIR, f"evidence_{day}.jsonl")
    if not os.path.exists(src):
        print(f"[EXPORT] no evidence file for {day}")
        return ""

    # aggregate per account
    acct = defaultdict(lambda: {
        "joins": 0, "kills": 0,
        "block_attempts": 0, "block_fails": 0,
        "goon_vault_hit": False, "gator_hit": False, "ee_signature": False,
        "joined_during_ghost_lock": False,
        "recognitions": [],       # list of "recognized_as (basis)"
        "human_decision": "",     # filled from DECISION events if present
        "reasons": set(),
    })

    with open(src, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            u = r.get("username")
            if not u:
                continue
            a = acct[u]
            ev = r.get("event")
            if ev == "JOIN":
                a["joins"] += 1
                a["goon_vault_hit"] |= r.get("goon_vault_hit", False)
                a["gator_hit"] |= r.get("gator_hit", False)
                a["ee_signature"] |= r.get("ee_signature", False)
                a["joined_during_ghost_lock"] |= r.get("joined_during_ghost_lock", False)
            elif ev == "CWIS_KILL":
                a["kills"] += 1
                if r.get("reason"):
                    a["reasons"].add(r["reason"])
            elif ev == "BLOCK_RESULT":
                a["block_attempts"] += 1
                if not r.get("block_success"):
                    a["block_fails"] += 1
            elif ev == "RECOGNITION":
                a["recognitions"].append(
                    f"{r.get('recognized_as','?')} ({r.get('basis','')})"
                )
            elif ev == "DECISION":
                a["human_decision"] = r.get("decision", "")

    out = out_path or os.path.join(INGEST_DIR, f"report_{day}.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        # header -- OBSERVED cols, then SUGGESTED, then RECOGNITION, then the
        # HUMAN DECISION column (last, because it's the point).
        w.writerow([
            "handle",
            # OBSERVED (verifiable facts)
            "joins", "cwis_kills", "block_attempts", "block_failures",
            "joined_during_ghost_lock",
            # SUGGESTED (weak flags -- NOT verdicts)
            "goon_vault_hit", "gator_hit", "ee_signature",
            "engine_reasons",
            # RECOGNITION (human input, unverified)
            "host_recognition",
            # >>> THE HUMAN DECISION -- host fills this <<<
            "DECISION (BLOCK/SAFE/WATCH)",
            "notes",
        ])
        # sort so the accounts with the strongest OBSERVED signal float up
        def score(item):
            a = item[1]
            return (a["block_fails"], a["kills"], a["joins"])
        for u, a in sorted(acct.items(), key=score, reverse=True):
            w.writerow([
                u,
                a["joins"], a["kills"], a["block_attempts"], a["block_fails"],
                "YES" if a["joined_during_ghost_lock"] else "",
                "YES" if a["goon_vault_hit"] else "",
                "YES" if a["gator_hit"] else "",
                "YES" if a["ee_signature"] else "",
                "; ".join(sorted(a["reasons"])),
                " | ".join(a["recognitions"]),
                a["human_decision"],     # pre-filled if host already decided
                "",                       # notes -- host fills
            ])

    print(f"[EXPORT] report written: {out}  ({len(acct)} accounts)")
    print("[EXPORT] NOTE: the DECISION column is intentionally blank -- the")
    print("[EXPORT] host makes the final call. The system presents evidence,")
    print("[EXPORT] it does not deliver verdicts.")
    return out


# ---------------------------------------------------------------------------
# CLI: build a report from the command line.
#   python aegis_ingest.py report                # today
#   python aegis_ingest.py report 2026-07-11     # a specific day
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "report":
        d = sys.argv[2] if len(sys.argv) >= 3 else None
        build_report(date=d)
    else:
        print("usage: python aegis_ingest.py report [YYYY-MM-DD]")
