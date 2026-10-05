"""寻货暂停 TTL 对齐测试:
- paused 期间到期扫不得释放 (行仍认领中, mine 仍挂原认领人)
- 未暂停的到期锁照常释放
- 冻结剩余秒: 投影不随 now 流逝; resume 按冻结剩余秒重算
- paused 期间 fulfill / claim 一律失败, 与按钮灰态同口径
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient


def iso(dt: datetime) -> str:
    return dt.isoformat()


NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)


# ---------- 引擎纯函数 ----------

from app.engines.claim_lock import release_if_expired  # noqa: E402
from app.engines.projection import remaining_seconds  # noqa: E402
from app.engines.sourcing_pause import PAUSED, action_allowed, resume_payload  # noqa: E402


def test_sweep_never_releases_paused_even_when_deadline_passed():
    past = iso(NOW - timedelta(hours=1))
    assert release_if_expired(PAUSED, past, NOW) is None


def test_sweep_still_releases_expired_claimed():
    past = iso(NOW - timedelta(seconds=1))
    rel = release_if_expired("claimed", past, NOW)
    assert rel == {"status": "open", "claimer": None, "claimed_at": None, "expires_at": None}


def test_sweep_keeps_live_claimed():
    future = iso(NOW + timedelta(hours=1))
    assert release_if_expired("claimed", future, NOW) is None


def test_paused_remaining_is_frozen_constant_independent_of_now():
    expires = iso(NOW + timedelta(hours=10))
    paused_at = iso(NOW - timedelta(hours=2))
    frozen = 10 * 3600 + 2 * 3600  # expires - paused_at
    assert remaining_seconds(PAUSED, expires, paused_at, NOW) == frozen
    # 暂停再久 (now 推进 5 小时), 剩余秒不往下跳
    assert remaining_seconds(PAUSED, expires, paused_at, NOW + timedelta(hours=5)) == frozen
    # 即便 now 已越过冻结截止时刻, 仍返回冻结常数而不是 0
    assert remaining_seconds(PAUSED, expires, paused_at, NOW + timedelta(days=3)) == frozen


def test_claimed_remaining_flows_with_time():
    expires = iso(NOW + timedelta(seconds=100))
    assert remaining_seconds("claimed", expires, None, NOW) == 100
    assert remaining_seconds("claimed", expires, None, NOW + timedelta(seconds=30)) == 70


def test_resume_recomputes_deadline_from_frozen_remaining():
    # 暂停时: 截止 = paused_at + 3600s (冻结剩余 1 小时)
    paused_at = iso(NOW - timedelta(hours=5))
    expires_frozen = iso(NOW - timedelta(hours=4))
    out = resume_payload(expires_frozen, paused_at, NOW)
    assert out["status"] == "claimed"
    assert out["paused_at"] is None
    # 恢复时刻 NOW 重新起算, 而不是接着暂停前的绝对时刻 (那已是过去)
    assert out["expires_at"] == iso(NOW + timedelta(seconds=3600))


def test_paused_actions_are_uniformly_denied():
    assert action_allowed(PAUSED, "fulfill")["ok"] is False
    assert action_allowed(PAUSED, "transfer")["ok"] is False
    assert action_allowed(PAUSED, "claim")["ok"] is False
    # 恢复后一切照常
    assert action_allowed("claimed", "fulfill")["ok"] is True


# ---------- API 集成 (临时库, 不碰真实数据) ----------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_seed_expired_claimed_gets_released_but_paused_sample_stays(client):
    rows = client.get("/api/wishes").json()
    by_id = {r["id"]: r for r in rows}
    ghost = next(r for r in rows if r["title"] == "过期锁样例")
    paused = next(r for r in rows if r["title"] == "暂停样例")

    # 未暂停的到期锁: 释放为 open, mine 不再挂 ghost
    assert ghost["status"] == "open"
    assert ghost["claimer"] is None
    assert client.get("/api/mine", params={"claimer": "ghost"}).json() == []

    # 暂停样例: 仍是认领中, 仍挂 pauser, 剩余秒冻结
    assert paused["status"] == PAUSED
    assert paused["claimer"] == "pauser"
    mine_paused = client.get("/api/mine", params={"claimer": "pauser"}).json()
    assert len(mine_paused) == 1 and mine_paused[0]["id"] == paused["id"]
    assert paused["paused"] is True


def test_paused_row_whose_frozen_deadline_is_in_past_survives_sweep(client, tmp_path):
    # 直接造一条「暂停且冻结截止时刻已过」的行, 复现现场最严重的叠放场景
    from app.db import connect
    c = connect()
    c.execute(
        "INSERT INTO wishes(title,note,status,claimer,claimed_at,expires_at,paused_at,data_quality)"
        " VALUES (?,?,?,?,?,?,?,?)",
        ("暂停已过冻结点", "x", PAUSED, "alice", iso(NOW - timedelta(days=2)),
         iso(NOW - timedelta(days=1)), iso(NOW - timedelta(days=2)), "clean"),
    )
    c.commit(); c.close()

    row = next(r for r in client.get("/api/wishes").json() if r["claimer"] == "alice")
    assert row["status"] == PAUSED  # 没有被扫成 open
    detail = client.get(f"/api/wishes/{row['id']}").json()
    assert detail["status"] == PAUSED and detail["claimer"] == "alice"
    assert detail["remaining_seconds"] == 24 * 3600  # expires-paused_at 冻结常数
    mine = client.get("/api/mine", params={"claimer": "alice"}).json()
    assert [r["id"] for r in mine] == [row["id"]]
    # 墙卡不可领: 他人接管被拒
    assert client.post(f"/api/wishes/{row['id']}/claim", json={"claimer": "bob"}).status_code == 409
    # 核销被拒, 状态不得变成 fulfilled
    assert client.post(f"/api/wishes/{row['id']}/fulfill").status_code == 409
    after = client.get(f"/api/wishes/{row['id']}").json()
    assert after["status"] == PAUSED


def test_full_pause_resume_cycle_freezes_then_recomputes(client):
    wid = client.post("/api/wishes", json={"title": "耳机", "note": ""}).json()["id"]
    client.post(f"/api/wishes/{wid}/claim", json={"claimer": "alice"})
    r = client.post(f"/api/wishes/{wid}/pause", json={"claimer": "alice"})
    assert r.status_code == 200
    frozen = client.get(f"/api/wishes/{wid}").json()["remaining_seconds"]
    assert 86399 <= frozen <= 86400  # ttl_seconds 种子值, 刚暂停即冻结

    # 暂停期间: 核销 409, 他人认领 409, 状态停留
    assert client.post(f"/api/wishes/{wid}/fulfill").status_code == 409
    assert client.post(f"/api/wishes/{wid}/claim", json={"claimer": "bob"}).status_code == 409

    # resume: 新 expires_at 与冻结剩余秒一致
    rr = client.post(f"/api/wishes/{wid}/resume", json={"claimer": "alice"}).json()
    from app.engines.claim_lock import parse_ts
    detail = client.get(f"/api/wishes/{wid}").json()
    assert detail["status"] == "claimed" and detail["paused"] is False
    new_ttl = (parse_ts(rr["expires_at"]) - parse_ts(detail["expires_at"])).total_seconds()
    assert abs(new_ttl) < 1  # 同一时刻口径


def test_claim_on_paused_is_rejected_before_lock_overwrite(client):
    wid = client.post("/api/wishes", json={"title": "杯子", "note": ""}).json()["id"]
    client.post(f"/api/wishes/{wid}/claim", json={"claimer": "alice"})
    client.post(f"/api/wishes/{wid}/pause", json={"claimer": "alice"})
    resp = client.post(f"/api/wishes/{wid}/claim", json={"claimer": "bob"})
    assert resp.status_code == 409
    row = client.get(f"/api/wishes/{wid}").json()
    assert row["claimer"] == "alice" and row["status"] == PAUSED
