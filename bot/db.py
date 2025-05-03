# bot/db.py

import asyncpg

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
# Maximum number of colonies each member is allowed to register
MAX_COLONIES = 11
# Maximum number of members each alliance can have
MAX_MEMBERS  = 50
# Admin password for protected commands (set by environment in practice)
ADMIN_PASS   = "HAC#ER4LFElol567"

# ─────────────────────────────────────────────────────────────────────────────
# Database Initialization
# ─────────────────────────────────────────────────────────────────────────────
async def init_db_pool(database_url: str) -> asyncpg.Pool:
    """
    Create an asyncpg connection pool and ensure all required tables exist.

    :param database_url: The DSN for connecting to PostgreSQL.
    :return: A configured asyncpg Pool instance.
    """
    # Create a connection pool with a minimum of 1 and maximum of 5 connections
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)

    # Acquire a single connection and execute multiple CREATE TABLE statements
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
    # Return the initialized pool for use by the bot
    return pool

# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions for Database Operations
# ─────────────────────────────────────────────────────────────────────────────

async def alliance_exists(pool: asyncpg.Pool, name: str) -> bool:
    """
    Check if an alliance with the given name exists.

    :return: True if found, False otherwise.
    """
    return bool(await pool.fetchval(
        "SELECT 1 FROM alliances WHERE name=$1", name
    ))

async def all_alliances(pool: asyncpg.Pool) -> list[str]:
    """
    Retrieve a sorted list of all alliance names.

    :return: List of alliance name strings.
    """
    rows = await pool.fetch(
        "SELECT name FROM alliances ORDER BY name"
    )
    return [r["name"] for r in rows]

async def member_exists(
    pool: asyncpg.Pool,
    alliance: str,
    member: str
) -> bool:
    """
    Check if a member is already registered under a given alliance.

    :return: True if exists, False otherwise.
    """
    return bool(await pool.fetchval(
        "SELECT 1 FROM members WHERE alliance=$1 AND member=$2",
        alliance, member
    ))

async def colony_count(
    pool: asyncpg.Pool,
    alliance: str,
    member: str
) -> int:
    """
    Count how many colony records a member has.

    :return: Integer count of colonies.
    """
    return await pool.fetchval(
        "SELECT COUNT(*) FROM colonies WHERE alliance=$1 AND member=$2",
        alliance, member
    )

async def get_members_with_colonies(
    pool: asyncpg.Pool,
    alliance: str
) -> list[tuple[str, int, list[tuple[int,int,int]], int]]:
    """
    Retrieve each member’s name, colony count, list of colonies, and main SB.

    :return: List of tuples:
      (member_name, colony_count, [(starbase, x, y), ...], main_sb)
    """
    # Fetch members and their main SB
    mrows = await pool.fetch(
        "SELECT member, COALESCE(main_sb,0) AS main_sb"
        " FROM members WHERE alliance=$1 ORDER BY member",
        alliance
    )
    # Fetch all colony records for that alliance
    crows = await pool.fetch(
        "SELECT member, starbase, x, y"
        " FROM colonies WHERE alliance=$1"
        " ORDER BY member, starbase DESC, x, y",
        alliance
    )
    # Build a map from member -> list of colony tuples
    cmap = {r["member"]: [] for r in mrows}
    for c in crows:
        cmap[c["member"]].append((c["starbase"], c["x"], c["y"]))

    # Return the combined data per member
    return [
        (
            r["member"],
            len(cmap[r["member"]]),
            cmap[r["member"]],
            r["main_sb"]
        ) for r in mrows
    ]

async def set_main_sb(
    pool: asyncpg.Pool,
    alliance: str,
    member: str,
    sb: int
) -> None:
    """
    Update a member’s main starbase level in the members table.
    """
    await pool.execute(
        "UPDATE members SET main_sb=$1 WHERE alliance=$2 AND member=$3",
        sb, alliance, member
    )

async def set_active_alliance(
    pool: asyncpg.Pool,
    guild_id: str,
    alliance: str
) -> None:
    """
    Mark an alliance as active for a given Discord guild.
    Uses an UPSERT so updating the same guild_id replaces the old value.
    """
    await pool.execute(
        """
        INSERT INTO settings(guild_id, alliance)
        VALUES($1,$2)
        ON CONFLICT (guild_id)
        DO UPDATE SET alliance = EXCLUDED.alliance
        """,
        guild_id, alliance
    )

async def get_active_alliance(
    pool: asyncpg.Pool,
    guild_id: str
) -> str | None:
    """
    Retrieve the currently active alliance for a guild, if any.

    :return: Alliance name or None.
    """
    return await pool.fetchval(
        "SELECT alliance FROM settings WHERE guild_id=$1",
        guild_id
    )

async def get_current_war(
    pool: asyncpg.Pool,
    guild_id: str
):
    """
    Retrieve the active war record (enemy alliance + start time) for a guild.

    :return: A Record with fields `enemy_alliance`, `start_time`, or None.
    """
    return await pool.fetchrow(
        "SELECT enemy_alliance, start_time FROM wars WHERE guild_id=$1",
        guild_id
    )