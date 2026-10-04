from app.db import connect

def init_db():
    c = connect()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS wishes(
      id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, note TEXT, status TEXT,
      claimer TEXT, claimed_at TEXT, expires_at TEXT, paused_at TEXT, data_quality TEXT
    );
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
    """)
    # 存量库迁移: 补 paused_at 列
    cols = {r["name"] for r in c.execute("PRAGMA table_info(wishes)")}
    if "paused_at" not in cols:
        c.execute("ALTER TABLE wishes ADD COLUMN paused_at TEXT")
        c.commit()
    if c.execute("SELECT COUNT(*) c FROM wishes").fetchone()["c"] == 0:
        c.executemany(
            "INSERT INTO wishes(title,note,status,claimer,claimed_at,expires_at,paused_at,data_quality) VALUES (?,?,?,?,?,?,?,?)",
            [
                ("机械键盘", "红轴", "open", None, None, None, None, "clean"),
                ("围巾", "羊毛", "open", None, None, None, None, "clean"),
                ("脏愿望-空标题", "", "open", None, None, None, None, "dirty"),
                ("过期锁样例", "应被TTL释放", "claimed", "ghost", "2020-01-01T00:00:00+00:00",
                 "2020-01-01T01:00:00+00:00", None, "dirty"),
                ("暂停样例", "寻货暂停中,TTL冻结", "sourcing_paused", "pauser",
                 "2026-10-01T00:00:00+00:00", "2026-12-01T00:00:00+00:00",
                 "2026-10-02T00:00:00+00:00", "clean"),
            ],
        )
        c.execute("INSERT INTO settings(key,value) VALUES ('ttl_seconds','86400')")
        c.execute("INSERT INTO settings(key,value) VALUES ('wall_title','暖粉愿望墙')")
        c.commit()
    c.close()
