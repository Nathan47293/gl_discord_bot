# -*- coding: utf-8 -*-
"""
Galaxy Life Alliance Tracker Bot — **PostgreSQL + Starbase edition**
===================================================================
Persistent across every deploy **without volumes**. Data is stored in the
free Railway **PostgreSQL** plugin; nothing writes to the container’s disk.

Key points
----------
* Now tracking three values per colony: X, Y, and Starbase level (1–9).
* Tables auto-create and auto-migrate on startup.
* Same slash-command API you already use.

Add these to requirements.txt:
    discord.py>=2.3
    asyncpg>=0.29

Env vars needed:
    DISCORD_BOT_TOKEN – your bot token
    DATABASE_URL      – Railway Postgres plugin URL
    TEST_GUILD_ID     – (optional) guild ID for instant slash-command sync
"""

import os
from typing import List, Tuple

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

MAX_COLONIES = 11
MIN_STARBASE = 1
MAX_STARBASE = 9

# ---------------------------------------------------------------------------
# Configuration checks
# ---------------------------------------------------------------------------
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Set the DISCORD_BOT_TOKEN env var.")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Set DATABASE_URL via your Postgres plugin.")

TEST_GUILD: discord.Object | None = None
if test_id := os.getenv("TEST_GUILD_ID"):
    try:
        TEST_GUILD = discord.Object(int(test_id))
    except ValueError:
        print("WARNING: TEST_GUILD_ID must be an integer ID; ignoring.")

# ---------------------------------------------------------------------------
# Bot definition with asyncpg pool
# ---------------------------------------------------------------------------
intents = discord.Intents.default()

class GalaxyBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.pool: asyncpg.Pool | None = None

    async def setup_hook(self) -> None:
        # 1) open DB pool
        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        # 2) init or migrate schema
        await self._init_db()

        # 3) sync commands
        if TEST_GUILD:
            self.tree.clear_commands(guild=TEST_GUILD)
            self.tree.copy_global_to(guild=TEST_GUILD)
            await self.tree.sync(guild=TEST_GUILD)
            print(f"❇ Commands synced to test guild {TEST_GUILD.id}")
            # also ensure global commands exist:
            await self.tree.sync()
            print("✅ Also synced globally")
        else:
            await self.tree.sync()
            print("✅ Global commands synced")

    async def _init_db(self) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            # create tables if missing
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS alliances (
                    name TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS members (
                    alliance TEXT REFERENCES alliances(name) ON DELETE CASCADE,
                    member   TEXT,
                    PRIMARY KEY(alliance, member)
                );
                CREATE TABLE IF NOT EXISTS colonies (
                    alliance  TEXT,
                    member    TEXT,
                    x         INT,
                    y         INT,
                    starbase  INT,
                    PRIMARY KEY(alliance, member, x, y),
                    FOREIGN KEY(alliance, member)
                        REFERENCES members(alliance, member)
                        ON DELETE CASCADE
                );
            """)
            # migrate existing table (if you added starbase later)
            await conn.execute("""
                ALTER TABLE colonies
                ADD COLUMN IF NOT EXISTS starbase INT;
            """)

bot = GalaxyBot()

# ---------------------------------------------------------------------------
# Database helper functions
# ---------------------------------------------------------------------------

async def alliance_exists(name: str) -> bool:
    async with bot.pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT 1 FROM alliances WHERE name=$1", name
        ) is not None

async def member_exists(alliance: str, member: str) -> bool:
    async with bot.pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT 1 FROM members WHERE alliance=$1 AND member=$2",
            alliance, member
        ) is not None

async def colony_count(alliance: str, member: str) -> int:
    async with bot.pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM colonies WHERE alliance=$1 AND member=$2",
            alliance, member
        )

async def all_alliances() -> List[str]:
    async with bot.pool.acquire() as conn:
        rows = await conn.fetch("SELECT name FROM alliances ORDER BY name")
    return [r["name"] for r in rows]

async def get_members_with_colonies(alliance: str) -> List[Tuple[str,int,List[Tuple[int,int,int]]]]:
    query = """
        SELECT m.member,
               COUNT(c.x)      AS ncol,
               COALESCE(
                 array_agg(c.x || ',' || c.y || ',' || c.starbase
                   ORDER BY c.x, c.y, c.starbase)
                 FILTER (WHERE c.x IS NOT NULL),
                 '{}'
               ) AS coords
        FROM members m
             LEFT JOIN colonies c
               ON c.alliance = m.alliance AND c.member = m.member
        WHERE m.alliance = $1
        GROUP BY m.member
        ORDER BY m.member;
    """
    async with bot.pool.acquire() as conn:
        rows = await conn.fetch(query, alliance)
    out: List[Tuple[str,int,List[Tuple[int,int,int]]]] = []
    for r in rows:
        coords = []
        for coord in r["coords"]:
            x_s, y_s, sb_s = coord.split(",")
            coords.append((int(x_s), int(y_s), int(sb_s)))
        out.append((r["member"], r["ncol"], coords))
    return out

# ---------------------------------------------------------------------------
# Autocomplete helpers
# ---------------------------------------------------------------------------

async def alliance_ac(inter: discord.Interaction, current: str):
    names = await all_alliances()
    return [
        app_commands.Choice(name=n, value=n)
        for n in names if current.lower() in n.lower()
    ][:25]

def member_ac_factory(param_alliance: str):
    async def _ac(inter: discord.Interaction, current: str):
        val = getattr(inter.namespace, param_alliance, None)
        if not val:
            return []
        async with bot.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT member FROM members WHERE alliance=$1 ORDER BY member",
                val
            )
        return [
            app_commands.Choice(name=r["member"], value=r["member"])
            for r in rows if current.lower() in r["member"].lower()
        ][:25]
    return _ac

# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

@bot.tree.command(description="Create a new alliance.")
@app_commands.describe(name="Alliance name")
async def addalliance(inter: discord.Interaction, name: str):
    if await alliance_exists(name):
        return await inter.response.send_message("That alliance already exists.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute("INSERT INTO alliances(name) VALUES($1)", name)
    await inter.response.send_message(f"✅ Alliance **{name}** registered!", ephemeral=True)

@bot.tree.command(description="Add a member to an alliance.")
@app_commands.autocomplete(alliance=alliance_ac)
@app_commands.describe(alliance="Alliance", member="Member name")
async def addmember(inter: discord.Interaction, alliance: str, member: str):
    if not await alliance_exists(alliance):
        return await inter.response.send_message("Alliance not found.", ephemeral=True)
    if await member_exists(alliance, member):
        return await inter.response.send_message("Member already exists.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO members(alliance, member) VALUES($1,$2)",
            alliance, member
        )
    await inter.response.send_message("✅ Member added.", ephemeral=True)

@bot.tree.command(description="Add a colony with X, Y and starbase level (1–9).")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac_factory("alliance"))
@app_commands.describe(
    alliance="Alliance",
    member="Member",
    x="X coordinate",
    y="Y coordinate",
    sb="Starbase level (1–9)"
)
async def addcolony(
    inter: discord.Interaction,
    alliance: str,
    member: str,
    x: int,
    y: int,
    sb: int
):
    if not await member_exists(alliance, member):
        return await inter.response.send_message("Member not found.", ephemeral=True)
    if sb < MIN_STARBASE or sb > MAX_STARBASE:
        return await inter.response.send_message(f"Starbase must be {MIN_STARBASE}–{MAX_STARBASE}.", ephemeral=True)
    if await colony_count(alliance, member) >= MAX_COLONIES:
        return await inter.response.send_message("Member already has 11 colonies.", ephemeral=True)

    async with bot.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO colonies(alliance, member, x, y, starbase) VALUES($1,$2,$3,$4,$5)",
            alliance, member, x, y, sb
        )
    await inter.response.send_message(f"✅ Colony `{x},{y}` (SB{sb}) added.", ephemeral=True)

@bot.tree.command(description="Show an alliance’s members & colonies.")
@app_commands.autocomplete(alliance=alliance_ac)
async def show(inter: discord.Interaction, alliance: str):
    if not await alliance_exists(alliance):
        return await inter.response.send_message("Alliance not found.", ephemeral=True)
    data = await get_members_with_colonies(alliance)

    embed = discord.Embed(
        title=f"{alliance} ({len(data)}/50 members)",
        color=discord.Color.blue()
    )
    if not data:
        embed.description = "No members recorded."
    else:
        for member, cnt, coords in data:
            line = ", ".join(f"{x},{y} (SB{sb})" for x,y,sb in coords) or "None"
            embed.add_field(name=f"{member} ({cnt}/{MAX_COLONIES})", value=line, inline=False)

    await inter.response.send_message(embed=embed)

@bot.tree.command(description="List all alliances.")
async def list(inter: discord.Interaction):
    names = await all_alliances()
    if not names:
        return await inter.response.send_message("No alliances recorded.", ephemeral=True)
    await inter.response.send_message("\n".join(f"- {n}" for n in names))

@bot.tree.command(description="Delete an alliance (admin only).")
@app_commands.autocomplete(alliance=alliance_ac)
async def reset(inter: discord.Interaction, alliance: str):
    if not await alliance_exists(alliance):
        return await inter.response.send_message("Alliance not found.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute("DELETE FROM alliances WHERE name=$1", alliance)
    await inter.response.send_message("✅ Alliance deleted.", ephemeral=True)

@bot.tree.command(description="Remove a member (and their colonies).")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac_factory("alliance"))
async def removemember(inter: discord.Interaction, alliance: str, member: str):
    if not await member_exists(alliance, member):
        return await inter.response.send_message("Member not found.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute("DELETE FROM members WHERE alliance=$1 AND member=$2", alliance, member)
    await inter.response.send_message(f"✅ Member **{member}** removed.", ephemeral=True)

@bot.tree.command(description="Remove a single colony.")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac_factory("alliance"))
@app_commands.describe(alliance="Alliance", member="Member", x="X coord", y="Y coord", sb="Starbase level")
async def removecolony(
    inter: discord.Interaction,
    alliance: str,
    member: str,
    x: int,
    y: int,
    sb: int
):
    if not await member_exists(alliance, member):
        return await inter.response.send_message("Member not found.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        res = await conn.execute(
            "DELETE FROM colonies WHERE alliance=$1 AND member=$2 AND x=$3 AND y=$4 AND starbase=$5",
            alliance, member, x, y, sb
        )
    if res.endswith("0"):
        return await inter.response.send_message("No matching colony found.", ephemeral=True)
    await inter.response.send_message(f"✅ Colony `{x},{y}` (SB{sb}) removed.", ephemeral=True)

@bot.tree.command(description="Rename a member.")
@app_commands.autocomplete(alliance=alliance_ac, old=member_ac_factory("alliance"))
async def renamemember(
    inter: discord.Interaction,
    alliance: str,
    old: str,
    new: str
):
    if not await member_exists(alliance, old):
        return await inter.response.send_message("Original member not found.", ephemeral=True)
    if await member_exists(alliance, new):
        return await inter.response.send_message("That new name already exists.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute(
            "UPDATE members SET member=$1 WHERE alliance=$2 AND member=$3",
            new, alliance, old
        )
        await conn.execute(
            "UPDATE colonies SET member=$1 WHERE alliance=$2 AND member=$3",
            new, alliance, old
        )
    await inter.response.send_message(f"✅ `{old}` renamed to `{new}`.", ephemeral=True)

# ---------------------------------------------------------------------------
# Run the bot
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    bot.run(TOKEN)
