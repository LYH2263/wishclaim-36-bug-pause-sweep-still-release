"""Sourcing pause state machine: claimed <-> sourcing_paused.

拍板口径:
1. expires_at 重算 —— 剩余秒冻结续跑: pause 时 expires_at 列不动、记 paused_at;
   resume 时 expires_at = now + (expires_at - paused_at)。
2. paused 期间禁令 —— transfer(他人认领/接管) 与 fulfill(核销) 同禁,
   仅当前认领人可 resume 或 release。
"""
from datetime import datetime, timedelta

from app.engines.claim_lock import parse_ts

PAUSED = "sourcing_paused"


def pause_allowed(status: str, claimer: str | None, actor: str) -> dict:
    """Only the current claimer of an active claim may pause."""
    if status != "claimed":
        return {"ok": False, "reason": "not_claimed"}
    if not claimer or claimer != actor:
        return {"ok": False, "reason": "not_claimer"}
    return {"ok": True, "reason": ""}


def pause_payload(now: datetime) -> dict:
    """Freeze the clock: expires_at stays as the frozen deadline."""
    return {"status": PAUSED, "paused_at": now.isoformat()}


def resume_allowed(status: str, claimer: str | None, actor: str) -> dict:
    """Only the current claimer of a paused claim may resume."""
    if status != PAUSED:
        return {"ok": False, "reason": "not_paused"}
    if not claimer or claimer != actor:
        return {"ok": False, "reason": "not_claimer"}
    return {"ok": True, "reason": ""}


def resume_payload(expires_at: str, paused_at: str, now: datetime) -> dict:
    """剩余秒冻结续跑: new expiry = now + remaining frozen at pause time."""
    remaining = parse_ts(expires_at) - parse_ts(paused_at)
    if remaining < timedelta(0):
        remaining = timedelta(0)
    return {
        "status": "claimed",
        "paused_at": None,
        "expires_at": (now + remaining).isoformat(),
    }


def action_allowed(status: str, action: str) -> dict:
    """拍板: paused 期间 transfer 与 fulfill 同禁, 仅 resume/release 放行."""
    if status == PAUSED and action in ("transfer", "claim", "fulfill"):
        return {"ok": False, "reason": "sourcing_paused"}
    return {"ok": True, "reason": ""}
