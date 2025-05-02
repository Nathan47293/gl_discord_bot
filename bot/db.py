# bot/db.py
import asyncpg

MAX_COLONIES = 11
MAX_MEMBERS  = 50
ADMIN_PASS   = "HAC#ER4LFElol567"

async def init_db_pool(database_url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS alliances (
          name TEXT PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS members (
          alliance TEXT REFERENCES alliances(name) ON DELETE CASCADE,
          member   TEXT,
          main_sb  INT,
          PRIMARY KEY(alliance, member)
        );
        CREATE TABLE IF NOT EXISTS settings (
          guild_id TEXT PRIMARY KEY,
          alliance TEXT REFERENCES alliances(name) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS colonies (
          id        SERIAL PRIMARY KEY,
          alliance  TEXT NOT NULL REFERENCES alliances(name) ON DELETE CASCADE,
          member    TEXT NOT NULL,
          starbase  INT NOT NULL,
          x         INT NOT NULL,
          y         INT NOT NULL,
          FOREIGN KEY(alliance, member)
            REFERENCES members(alliance, member)
            ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS wars (
          guild_id       TEXT PRIMARY KEY REFERENCES settings(guild_id) ON DELETE CASCADE,
          enemy_alliance TEXT NOT NULL REFERENCES alliances(name),
          start_time     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS war_attacks (
          guild_id    TEXT NOT NULL REFERENCES wars(guild_id) ON DELETE CASCADE,
          member      TEXT NOT NULL,
          last_attack TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY (guild_id, member)
        );
        """)
    return pool

# ——— Helpers ———

async def alliance_exists(pool, name: str) -> bool:
    return bool(await pool.fetchval("SELECT 1 FROM alliances WHERE name=$1", name))

async def all_alliances(pool) -> list[str]:
    rows = await pool.fetch("SELECT name FROM alliances ORDER BY name")
    return [r["name"] for r in rows]

async def member_exists(pool, alliance: str, member: str) -> bool:
    return bool(await pool.fetchval(
        "SELECT 1 FROM members WHERE alliance=$1 AND member=$2",
        alliance, member
    ))

async def colony_count(pool, alliance: str, member: str) -> int:
    return await pool.fetchval(
        "SELECT COUNT(*) FROM colonies WHERE alliance=$1 AND member=$2",
        alliance, member
    )

async def get_members_with_colonies(pool, alliance: str):
    mrows = await pool.fetch(
        "SELECT member, COALESCE(main_sb,0) AS main_sb FROM members WHERE alliance=$1 ORDER BY member",
        alliance
    )
    crows = await pool.fetch(
        "SELECT member, starbase, x, y FROM colonies WHERE alliance=$1 ORDER BY member, starbase DESC, x, y",
        alliance
    )
    cmap = {r["member"]: [] for r in mrows}
    for c in crows:
        cmap[c["member"]].append((c["starbase"], c["x"], c["y"]))
    return [
        (r["member"], len(cmap[r["member"]]), cmap[r["member"]], r["main_sb"])
        for r in mrows
    ]

async def set_main_sb(pool, alliance: str, member: str, sb: int):
    await pool.execute(
        "UPDATE members SET main_sb=$1 WHERE alliance=$2 AND member=$3",
        sb, alliance, member
    )

async def set_active_alliance(pool, guild_id: str, alliance: str):
    await pool.execute("""
      INSERT INTO settings(guild_id, alliance)
      VALUES($1,$2)
      ON CONFLICT (guild_id) DO UPDATE SET alliance = EXCLUDED.alliance
    """, guild_id, alliance)

async def get_active_alliance(pool, guild_id: str) -> str | None:
    return await pool.fetchval(
        "SELECT alliance FROM settings WHERE guild_id=$1", guild_id
    )

async def get_current_war(pool, guild_id: str):
    return await pool.fetchrow(
        "SELECT enemy_alliance, start_time FROM wars WHERE guild_id=$1", guild_id
    )
