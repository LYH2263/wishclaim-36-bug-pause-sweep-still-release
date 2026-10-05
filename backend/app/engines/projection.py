"""Read-model projection: remaining seconds + countdown label for wishes.

墙倒计时、详情剩余秒、规则说明三路同源的单一计算入口:
- claimed         -> 剩余 = expires_at - now (随时间流逝)
- sourcing_paused -> 剩余 = expires_at - paused_at (冻结常数, 不流逝)
"""
from datetime import datetime

from app.engines.claim_lock import parse_ts
from app.engines.sourcing_pause import PAUSED


def remaining_seconds(status: str, expires_at: str | None, paused_at: str | None, now: datetime) -> int | None:
    # claimed: 随真实时间流逝
    if status == "claimed" and expires_at:
        return max(0, int((parse_ts(expires_at) - now).total_seconds()))
    # sourcing_paused: 冻结常数 = 暂停瞬间剩余 (expires_at - paused_at), 与 now 无关,
    # 暂停多久都不往下跳; resume 时正是按这个值续算 expires_at。
    if status == PAUSED and expires_at and paused_at:
        return max(0, int((parse_ts(expires_at) - parse_ts(paused_at)).total_seconds()))
    return None


def wish_projection(row: dict, now: datetime) -> dict:
    rem = remaining_seconds(row["status"], row.get("expires_at"), row.get("paused_at"), now)
    paused = row["status"] == PAUSED
    return {
        "paused": paused,
        "remaining_seconds": rem,
        "countdown": ("已暂停 · 剩余 %ds" % rem) if paused and rem is not None
                     else ("剩余 %ds" % rem) if rem is not None else "",
    }
