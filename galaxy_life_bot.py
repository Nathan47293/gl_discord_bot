# -*- coding: utf-8 -*-
"""
Galaxy Life Alliance Tracker Bot — PostgreSQL + Starbase
========================================================
Persistent across every deploy (no volumes). Data in Railway Postgres.

Requirements (requirements.txt):
    discord.py>=2.3
    asyncpg>=0.29

Env vars:
    DISCORD_BOT_TOKEN – your bot token
    DATABASE_URL      – Railway Postgres URL
    TEST_GUILD_ID     – optional guild ID for instant slash-command sync
"""

import os
from typing import List, Tuple

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MAX_COLONIES = 11
MIN_STARBASE = 1
MAX_STARBASE = 9

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Set DISCORD_BOT_TOKEN")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Set DATABASE_URL")

_TEST_GUILD = os.getenv("TEST_GUILD_ID")
TEST_GUILD: discord.Object | None = None
if _TEST_GUILD:
    try:
        TEST_GUILD = discord.Object(int(_TEST_GUILD))
    except ValueError:
        print("⚠️ TEST_GUILD_ID is invalid, ignoring")

# ---------------------------------------------------------------------------
# Bot + DB
# ---------------------------------------------------------------------------
intents = discord.Intents.default()

class GalaxyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.pool: asyncpg.Pool | None = None

    async def setup_hook(self):
        # open DB + migrate schema
        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        await self._init_db()

        # register commands
        if TEST_GUILD:
            self.tree.clear_commands(guild=TEST_GUILD)
            self.tree.copy_global_to(guild=TEST_GUILD)
            await self.tree.sync(guild=TEST_GUILD)
            print(f"✅ Commands synced to test guild {TEST_GUILD.id}")
        else:
            await self.tree.sync()
            print("✅ Global commands synced")

    async def _init_db(self):
        assert self.pool
        async with self.pool.acquire() as c:
            # create alliances + members
            await c.execute("""
            CREATE TABLE IF NOT EXISTS alliances (
              name TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS members (
              alliance TEXT REFERENCES alliances(name) ON DELETE CASCADE,
              member   TEXT,
              PRIMARY KEY(alliance, member)
            );
            -- colonies: PK on alliance,member,x,y; starbase is a column
            CREATE TABLE IF NOT EXISTS colonies (
              alliance TEXT,
              member   TEXT,
              x        INT,
              y        INT,
              starbase INT,
              PRIMARY KEY(alliance, member, x, y),
              FOREIGN KEY(alliance, member)
                REFERENCES members(alliance, member)
                ON DELETE CASCADE
            );
            """)
            # back-fill starbase for any existing rows (old table might have none)
            await c.execute("""
              ALTER TABLE colonies
              ADD COLUMN IF NOT EXISTS starbase INT NOT NULL DEFAULT 1
            """)

bot = GalaxyBot()

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
async def alliance_exists(name: str) -> bool:
    async with bot.pool.acquire() as c:
        return await c.fetchval("SELECT 1 FROM alliances WHERE name=$1", name) is not None

async def member_exists(alliance: str, member: str) -> bool:
    async with bot.pool.acquire() as c:
        return await c.fetchval(
            "SELECT 1 FROM members WHERE alliance=$1 AND member=$2",
            alliance, member
        ) is not None

async def colony_count(alliance: str, member: str) -> int:
    async with bot.pool.acquire() as c:
        return await c.fetchval(
            "SELECT COUNT(*) FROM colonies WHERE alliance=$1 AND member=$2",
            alliance, member
        )

async def all_alliances() -> List[str]:
    async with bot.pool.acquire() as c:
        return [r["name"] for r in await c.fetch("SELECT name FROM alliances ORDER BY name")]

async def get_members_with_colonies(alliance: str) -> List[Tuple[str,int,List[Tuple[int,int,int]]]]:
    async with bot.pool.acquire() as c:
        rows = await c.fetch("""
            SELECT m.member,
                   COUNT(c.x) AS ncol,
                   COALESCE(
                     array_agg(c.x||','||c.y||','||c.starbase
                       ORDER BY c.x,c.y)
                     FILTER (WHERE c.x IS NOT NULL),
                     '{}'
                   ) AS coords
            FROM members m
            LEFT JOIN colonies c
              ON c.alliance=m.alliance AND c.member=m.member
            WHERE m.alliance=$1
            GROUP BY m.member
            ORDER BY m.member;
        """, alliance)

    out: List[Tuple[str,int,List[Tuple[int,int,int]]]] = []
    for r in rows:
        coords: List[Tuple[int,int,int]] = []
        for text in r["coords"] or []:
            x_str, y_str, sb_str = text.split(",")
            coords.append((int(x_str), int(y_str), int(sb_str)))
        out.append((r["member"], r["ncol"], coords))
    return out

# ---------------------------------------------------------------------------
# Autocomplete
# ---------------------------------------------------------------------------
async def alliance_ac(inter: discord.Interaction, current: str):
    names = await all_alliances()
    return [
      app_commands.Choice(name=n, value=n)
      for n in names if current.lower() in n.lower()
    ][:25]

def member_ac_factory(param: str):
    async def _ac(inter: discord.Interaction, current: str):
        val = getattr(inter.namespace, param, None)
        if not val:
            return []
        async with bot.pool.acquire() as c:
            rows = await c.fetch(
              "SELECT member FROM members WHERE alliance=$1 ORDER BY member",
              val
            )
        return [
          app_commands.Choice(name=r["member"], value=r["member"])
          for r in rows if current.lower() in r["member"].lower()
        ][:25]
    return _ac

# ---------------------------------------------------------------------------
# Slash-commands
# ---------------------------------------------------------------------------
@bot.tree.command(description="Create a new alliance.")
@app_commands.describe(name="Alliance name")
async def addalliance(inter: discord.Interaction, name: str):
    if await alliance_exists(name):
        return await inter.response.send_message("❌ Already exists.", ephemeral=True)
    async with bot.pool.acquire() as c:
        await c.execute("INSERT INTO alliances(name) VALUES($1)", name)
    await inter.response.send_message(f"✅ Alliance **{name}** registered.", ephemeral=True)

@bot.tree.command(description="Add a member to an alliance.")
@app_commands.autocomplete(alliance=alliance_ac)
@app_commands.describe(alliance="Alliance name", member="Member name")
async def addmember(inter: discord.Interaction, alliance: str, member: str):
    if not await alliance_exists(alliance):
        return await inter.response.send_message("❌ Alliance not found.", ephemeral=True)
    if await member_exists(alliance, member):
        return await inter.response.send_message("❌ Member exists.", ephemeral=True)
    async with bot.pool.acquire() as c:
        await c.execute(
            "INSERT INTO members(alliance, member) VALUES($1,$2)",
            alliance, member
        )
    await inter.response.send_message("✅ Member added.", ephemeral=True)

@bot.tree.command(description="Add or update a colony (SB, X, Y).")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac_factory("alliance"))
@app_commands.describe(
    alliance="Alliance",
    member="Member",
    sb="Starbase level (1–9)",
    x="X coord",
    y="Y coord"
)
async def addcolony(
    inter: discord.Interaction,
    alliance: str,
    member: str,
    sb: int,
    x: int,
    y: int
):
    if not await member_exists(alliance, member):
        return await inter.response.send_message("❌ Member not found.", ephemeral=True)
    if sb < MIN_STARBASE or sb > MAX_STARBASE:
        return await inter.response.send_message(f"❌ SB must be {MIN_STARBASE}–{MAX_STARBASE}.", ephemeral=True)
    cnt = await colony_count(alliance, member)
    exists = await bot.pool.fetchval(
        "SELECT 1 FROM colonies WHERE alliance=$1 AND member=$2 AND x=$3 AND y=$4",
        alliance, member, x, y
    )
    if cnt >= MAX_COLONIES and not exists:
        return await inter.response.send_message("❌ Max 11 distinct colonies reached.", ephemeral=True)

    # UPSERT the starbase level
    async with bot.pool.acquire() as c:
        await c.execute("""
            INSERT INTO colonies(alliance, member, x, y, starbase)
            VALUES($1,$2,$3,$4,$5)
            ON CONFLICT(alliance, member, x, y)
            DO UPDATE SET starbase = EXCLUDED.starbase
        """, alliance, member, x, y, sb)

    await inter.response.send_message(f"✅ SB{sb} @ {x},{y} saved.", ephemeral=True)

@bot.tree.command(description="Show members & colonies of an alliance.")
@app_commands.autocomplete(alliance=alliance_ac)
async def show(inter: discord.Interaction, alliance: str):
    if not await alliance_exists(alliance):
        return await inter.response.send_message("❌ Alliance not found.", ephemeral=True)
    data = await get_members_with_colonies(alliance)

    embed = discord.Embed(
        title=f"{alliance} ({len(data)}/50 members)",
        color=discord.Color.blue()
    )
    if not data:
        embed.description = "No members recorded."
    else:
        for mem, cnt, cols in data:
            line = ", ".join(f"SB{sb}@{x},{y}" for x,y,sb in cols) or "None"
            embed.add_field(name=f"{mem} ({cnt}/{MAX_COLONIES})", value=line, inline=False)

    await inter.response.send_message(embed=embed)

@bot.tree.command(description="List all alliances.")
async def list(inter: discord.Interaction):
    names = await all_alliances()
    if not names:
        return await inter.response.send_message("❌ No alliances yet.", ephemeral=True)
    await inter.response.send_message("\n".join(f"- {n}" for n in names))

@bot.tree.command(description="Delete an alliance (admin only).")
@app_commands.autocomplete(alliance=alliance_ac)
async def reset(inter: discord.Interaction, alliance: str):
    if not await alliance_exists(alliance):
        return await inter.response.send_message("❌ Alliance not found.", ephemeral=True)
    async with bot.pool.acquire() as c:
        await c.execute("DELETE FROM alliances WHERE name=$1", alliance)
    await inter.response.send_message("✅ Alliance deleted.", ephemeral=True)

@bot.tree.command(description="Remove a member (and their colonies).")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac_factory("alliance"))
async def removemember(inter: discord.Interaction, alliance: str, member: str):
    if not await member_exists(alliance, member):
        return await inter.response.send_message("❌ Member not found.", ephemeral=True)
    async with bot.pool.acquire() as c:
        await c.execute(
            "DELETE FROM members WHERE alliance=$1 AND member=$2",
            alliance, member
        )
    await inter.response.send_message(f"✅ Member **{member}** removed.", ephemeral=True)

@bot.tree.command(description="Remove a colony by SB, X, Y.")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac_factory("alliance"))
@app_commands.describe(alliance="Alliance", member="Member", sb="Starbase", x="X", y="Y")
async def removecolony(
    inter: discord.Interaction,
    alliance: str,
    member: str,
    sb: int,
    x: int,
    y: int
):
    if not await member_exists(alliance, member):
        return await inter.response.send_message("❌ Member not found.", ephemeral=True)
    async with bot.pool.acquire() as c:
        res = await c.execute("""
            DELETE FROM colonies
             WHERE alliance=$1 AND member=$2 AND x=$3 AND y=$4
        """, alliance, member, x, y)
    if res.endswith("0"):
        return await inter.response.send_message("❌ Colony not found.", ephemeral=True)
    await inter.response.send_message(f"✅ Colony @ {x},{y} removed.", ephemeral=True)

@bot.tree.command(description="Rename a member.")
@app_commands.autocomplete(alliance=alliance_ac, old=member_ac_factory("alliance"))
async def renamemember(inter: discord.Interaction, alliance: str, old: str, new: str):
    if not await member_exists(alliance, old):
        return await inter.response.send_message("❌ Original not found.", ephemeral=True)
    if await member_exists(alliance, new):
        return await inter.response.send_message("❌ New name taken.", ephemeral=True)
    async with bot.pool.acquire() as c:
        await c.execute(
            "UPDATE members SET member=$1 WHERE alliance=$2 AND member=$3",
            new, alliance, old
        )
        await c.execute(
            "UPDATE colonies SET member=$1 WHERE alliance=$2 AND member=$3",
            new, alliance, old
        )
    await inter.response.send_message(f"✅ `{old}` → `{new}`.", ephemeral=True)

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    bot.run(TOKEN)
