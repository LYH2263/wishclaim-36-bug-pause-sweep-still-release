from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect
from app.engines.claim_lock import claim_allowed, lock_payload, release_if_expired
from app.engines.projection import wish_projection
from app.engines.sourcing_pause import (
    PAUSED, action_allowed, pause_allowed, pause_payload, resume_allowed, resume_payload,
)

app = FastAPI(title="Wishclaim", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

def now(): return datetime.now(timezone.utc)

def ttl():
    c = connect(); row = c.execute("SELECT value FROM settings WHERE key='ttl_seconds'").fetchone(); c.close()
    return int(row["value"] if row else 86400)

def sweep(c):
    for r in c.execute("SELECT * FROM wishes WHERE status IN ('claimed','sourcing_paused')"):
        rel = release_if_expired(r["status"], r["expires_at"], now())
        if rel:
            # 原子落库 rel 全量字段(含 claimer=None): 不得出现 open 而 claimer 仍占原人的分裂态
            c.execute("UPDATE wishes SET status=?, claimer=?, claimed_at=?, expires_at=?, paused_at=NULL WHERE id=?",
                      (rel["status"], rel["claimer"], rel["claimed_at"], rel["expires_at"], r["id"]))

def proj(r: dict) -> dict:
    """Attach the shared countdown projection (wall/detail/rules 三路同源)."""
    return {**r, **wish_projection(r, now())}

@app.get("/api/health")
def health(): return {"ok": True, "project": "wishclaim"}

@app.get("/api/wishes")
def list_wishes():
    c = connect(); sweep(c); c.commit()
    rows = [proj(dict(r)) for r in c.execute("SELECT * FROM wishes ORDER BY id DESC")]; c.close(); return rows

@app.get("/api/wishes/{wid}")
def get_wish(wid: int):
    c = connect(); sweep(c); c.commit()
    r = c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone(); c.close()
    if not r: raise HTTPException(404, "not found")
    return proj(dict(r))

class WishIn(BaseModel):
    title: str
    note: str = ""

@app.post("/api/wishes")
def create_wish(body: WishIn):
    c = connect()
    cur = c.execute("INSERT INTO wishes(title,note,status,data_quality) VALUES (?,?,?,?)",
                    (body.title, body.note, "open", "clean"))
    c.commit(); wid = cur.lastrowid; c.close(); return {"id": wid}

class ClaimIn(BaseModel):
    claimer: str

@app.post("/api/wishes/{wid}/claim")
def claim(wid: int, body: ClaimIn):
    c = connect(); sweep(c); c.commit()
    r = c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone()
    if not r: c.close(); raise HTTPException(404, "not found")
    allowed = claim_allowed(r["status"], r["claimer"], now(), r["expires_at"])
    if not allowed["ok"]:
        c.close(); raise HTTPException(409, allowed["reason"])
    p = lock_payload(body.claimer, now(), ttl())
    c.execute("UPDATE wishes SET status=?, claimer=?, claimed_at=?, expires_at=?, paused_at=NULL WHERE id=?",
              (p["status"], p["claimer"], p["claimed_at"], p["expires_at"], wid))
    c.commit(); c.close(); return p

@app.post("/api/wishes/{wid}/release")
def release(wid: int):
    c = connect()
    r = c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone()
    if not r: c.close(); raise HTTPException(404, "not found")
    if r["status"] not in ("claimed", PAUSED):
        c.close(); raise HTTPException(400, "not_claimed")
    c.execute("UPDATE wishes SET status='released', claimer=NULL, claimed_at=NULL, expires_at=NULL, paused_at=NULL WHERE id=?", (wid,))
    c.commit(); c.close(); return {"ok": True, "status": "released"}

@app.post("/api/wishes/{wid}/fulfill")
def fulfill(wid: int):
    c = connect()
    r = c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone()
    if not r: c.close(); raise HTTPException(404, "not found")
    chk = action_allowed(r["status"], "fulfill")
    if not chk["ok"]:
        c.close(); raise HTTPException(409, chk["reason"])
    if r["status"] != "claimed":
        c.close(); raise HTTPException(400, "need_claim")
    c.execute("UPDATE wishes SET status='fulfilled' WHERE id=?", (wid,))
    c.commit(); c.close(); return {"ok": True, "status": "fulfilled"}

class ActorIn(BaseModel):
    claimer: str

@app.post("/api/wishes/{wid}/pause")
def pause(wid: int, body: ActorIn):
    c = connect(); sweep(c); c.commit()
    r = c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone()
    if not r: c.close(); raise HTTPException(404, "not found")
    chk = pause_allowed(r["status"], r["claimer"], body.claimer)
    if not chk["ok"]:
        c.close(); raise HTTPException(403 if chk["reason"] == "not_claimer" else 409, chk["reason"])
    p = pause_payload(now())
    c.execute("UPDATE wishes SET status=?, paused_at=? WHERE id=?", (p["status"], p["paused_at"], wid))
    c.commit(); c.close(); return p

@app.post("/api/wishes/{wid}/resume")
def resume(wid: int, body: ActorIn):
    c = connect()
    r = c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone()
    if not r: c.close(); raise HTTPException(404, "not found")
    chk = resume_allowed(r["status"], r["claimer"], body.claimer)
    if not chk["ok"]:
        c.close(); raise HTTPException(403 if chk["reason"] == "not_claimer" else 409, chk["reason"])
    p = resume_payload(r["expires_at"], r["paused_at"], now())
    c.execute("UPDATE wishes SET status=?, paused_at=?, expires_at=? WHERE id=?",
              (p["status"], p["paused_at"], p["expires_at"], wid))
    c.commit(); c.close(); return p

@app.get("/api/mine")
def mine(claimer: str):
    c = connect(); sweep(c); c.commit()
    rows = [proj(dict(r)) for r in c.execute("SELECT * FROM wishes WHERE claimer=?", (claimer,))]; c.close(); return rows

@app.get("/api/done")
def done():
    c = connect()
    rows = [dict(r) for r in c.execute("SELECT * FROM wishes WHERE status='fulfilled'")]; c.close(); return rows

@app.get("/api/settings")
def settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows

@app.get("/api/rules")
def rules():
    return {
        "mutex": "同一愿望同时只能被一人认领",
        "ttl": "认领超时未核销则自动释放",
        "fulfill": "核销后状态变为 fulfilled",
        "pause": "认领人可将认领中的愿望标记为 sourcing_paused(寻货暂停), 暂停期间 TTL 冻结, 扫尾不释放",
        "resume": "恢复后按暂停时冻结的剩余秒数续跑, expires_at = 恢复时刻 + 冻结剩余秒",
        "paused_limits": "暂停期间禁止转让(他人认领/接管)与核销, 仅当前认领人可继续寻货或释放",
    }
