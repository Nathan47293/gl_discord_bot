# -*- coding: utf-8 -*-
"""
Galaxy Life Alliance Tracker Bot — PostgreSQL Edition
----------------------------------------------------

Persistent across deploys: data lives in a Railway PostgreSQL plugin,
no container volumes needed.

Requirements (in requirements.txt):
    discord.py>=2.3
    asyncpg>=0.29

Env vars:
    DISCORD_BOT_TOKEN – your bot token
    DATABASE_URL      – Railway Postgres plugin URL
    TEST_GUILD_ID     – optional guild ID for instant slash-command sync

Schema auto-created:
    alliances(name TEXT PRIMARY KEY)
    members(alliance TEXT, member TEXT, PRIMARY KEY(alliance, member))
    colonies(alliance TEXT, member TEXT, sb INT, x INT, y INT,
             PRIMARY KEY(alliance, member, sb, x, y),
             FOREIGN KEY(alliance, member) REFERENCES members(alliance, member) ON DELETE CASCADE)
"""
from __future__ import annotations
import os
from typing import List, Tuple

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_COLONIES = 11
MAX_STARBASE = 9
MIN_STARBASE = 1

# ---------------------------------------------------------------------------
# Configuration checks
# ---------------------------------------------------------------------------
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Set DISCORD_BOT_TOKEN env var.")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Set DATABASE_URL env var.")

TEST_GUILD: discord.Object | None = None
if os.getenv("TEST_GUILD_ID"):
    try:
        TEST_GUILD = discord.Object(int(os.environ["TEST_GUILD_ID"]))
    except ValueError:
        print("WARNING: TEST_GUILD_ID is not an integer; ignoring.")

# ---------------------------------------------------------------------------
# Bot definition with asyncpg pool
# ---------------------------------------------------------------------------
intents = discord.Intents.default()

class GalaxyBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.pool: asyncpg.Pool | None = None

    async def setup_hook(self) -> None:
        # Initialize DB pool & schema
        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        await self._init_db()

        # Wipe out any cached commands so we never see duplicates
        self.tree.clear_commands(guild=None)
        if TEST_GUILD:
            self.tree.clear_commands(guild=TEST_GUILD)
            # Copy local commands to test guild only
            self.tree.copy_global_to(guild=TEST_GUILD)
            await self.tree.sync(guild=TEST_GUILD)
            print(f"❇ Cleared & re-synced slash-commands to test guild {TEST_GUILD.id}")
        else:
            print("✅ No TEST_GUILD_ID set — running without guild-only sync")

    async def _init_db(self) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            # Create tables with starbase (sb) and allow duplicates at same x,y if sb differs
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alliances (
                  name TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS members (
                  alliance TEXT REFERENCES alliances(name) ON DELETE CASCADE,
                  member   TEXT,
                  PRIMARY KEY(alliance, member)
                );
                CREATE TABLE IF NOT EXISTS colonies (
                  alliance TEXT,
                  member   TEXT,
                  sb       INT,
                  x        INT,
                  y        INT,
                  PRIMARY KEY(alliance, member, sb, x, y),
                  FOREIGN KEY(alliance, member) REFERENCES members(alliance, member) ON DELETE CASCADE
                );
                """
            )

bot = GalaxyBot()

# ---------------------------------------------------------------------------
# Database helper functions
# ---------------------------------------------------------------------------
async def alliance_exists(name: str) -> bool:
    async with bot.pool.acquire() as conn:
        return await conn.fetchval("SELECT 1 FROM alliances WHERE name=$1", name) is not None

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
    return [r[0] for r in rows]

async def get_members_with_colonies(alliance: str) -> List[Tuple[str,int,List[Tuple[int,int,int]]]]:
    # Fetch each member, count, and array of "sb,x,y" sorted by sb DESC
    query = """
        SELECT m.member,
               COUNT(c.sb) AS ncol,
               COALESCE(
                 array_agg(c.sb||','||c.x||','||c.y ORDER BY c.sb DESC, c.x, c.y)
                 FILTER (WHERE c.sb IS NOT NULL),
                 '{}'
               ) AS coords
        FROM members m
        LEFT JOIN colonies c
          ON c.alliance=m.alliance AND c.member=m.member
        WHERE m.alliance=$1
        GROUP BY m.member
        ORDER BY m.member;
    """
    async with bot.pool.acquire() as conn:
        rows = await conn.fetch(query, alliance)
    result: List[Tuple[str,int,List[Tuple[int,int,int]]]] = []
    for r in rows:
        # each r[2] is a Postgres text[] of "sb,x,y" strings
        coords = [tuple(map(int, s.split(','))) for s in r[2]] if r[2] else []
        result.append((r[0], r[1], coords))
    return result

# ---------------------------------------------------------------------------
# Autocomplete helpers
# ---------------------------------------------------------------------------
async def alliance_ac(inter: discord.Interaction, current: str):
    names = await all_alliances()
    cur = current.lower()
    return [
        app_commands.Choice(name=n, value=n)
        for n in names if cur in n.lower()
    ][:25]

def member_ac_factory(param: str):
    async def _ac(inter: discord.Interaction, current: str):
        alliance_val = getattr(inter.namespace, param, None)
        if not alliance_val:
            return []
        async with bot.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT member FROM members WHERE alliance=$1 ORDER BY member",
                alliance_val
            )
        cur = current.lower()
        return [
            app_commands.Choice(name=r[0], value=r[0])
            for r in rows if cur in r[0].lower()
        ][:25]
    return _ac

# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------
@bot.tree.command(description="Create a new alliance entry.")
@app_commands.describe(name="Alliance name")
async def addalliance(inter: discord.Interaction, name: str):
    if await alliance_exists(name):
        return await inter.response.send_message("❌ Alliance already exists.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute("INSERT INTO alliances(name) VALUES($1)", name)
    await inter.response.send_message(f"✅ Alliance **{name}** registered!", ephemeral=True)

@bot.tree.command(description="Add a member to an alliance.")
@app_commands.autocomplete(alliance=alliance_ac)
@app_commands.describe(alliance="Alliance name", member="Member name")
async def addmember(inter: discord.Interaction, alliance: str, member: str):
    if not await alliance_exists(alliance):
        return await inter.response.send_message("❌ Alliance not found.", ephemeral=True)
    if await member_exists(alliance, member):
        return await inter.response.send_message("❌ Member already exists.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO members(alliance, member) VALUES($1,$2)",
            alliance, member
        )
    await inter.response.send_message("✅ Member added.", ephemeral=True)

@bot.tree.command(description="Add a colony coordinate (max 11 per member).")
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
    if not (MIN_STARBASE <= sb <= MAX_STARBASE):
        return await inter.response.send_message(f"❌ Starbase must be {MIN_STARBASE}–{MAX_STARBASE}.", ephemeral=True)
    if await colony_count(alliance, member) >= MAX_COLONIES:
        return await inter.response.send_message("❌ Max colonies reached.", ephemeral=True)

    async with bot.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO colonies(alliance, member, sb, x, y) VALUES($1,$2,$3,$4,$5)",
            alliance, member, sb, x, y
        )
    await inter.response.send_message(
        f"✅ Colony sb{sb} ({x},{y}) added for **{member}**.", ephemeral=True
    )

@bot.tree.command(description="Show an alliance’s members & colonies.")
@app_commands.autocomplete(alliance=alliance_ac)
async def show(inter: discord.Interaction, alliance: str):
    if not await alliance_exists(alliance):
        return await inter.response.send_message("❌ Alliance not found.", ephemeral=True)

    members = await get_members_with_colonies(alliance)
    total = len(members)
    embed = discord.Embed(
        title=f"{alliance} ({total}/50 members)",
        color=discord.Color.blue()
    )

    if not members:
        embed.description = "No members recorded."
    else:
        for name, cnt, coords in members:
            lines = "\n".join(f"sb{sb} ({x},{y})" for sb,x,y in coords)
            embed.add_field(
                name=f"{name} ({cnt}/{MAX_COLONIES})",
                value=lines or "None",
                inline=False
            )

    await inter.response.send_message(embed=embed)

@bot.tree.command(name="list", description="List all alliances.")
async def list_alliances(inter: discord.Interaction):
    names = await all_alliances()
    if not names:
        return await inter.response.send_message("No alliances recorded.", ephemeral=True)
    await inter.response.send_message("\n".join(f"- {n}" for n in names))

@bot.tree.command(description="Delete an alliance (admin only).")
@app_commands.autocomplete(alliance=alliance_ac)
async def reset(inter: discord.Interaction, alliance: str):
    if not await alliance_exists(alliance):
        return await inter.response.send_message("❌ Alliance not found.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute("DELETE FROM alliances WHERE name=$1", alliance)
    await inter.response.send_message("✅ Alliance deleted.", ephemeral=True)

@bot.tree.command(description="Remove a member (and all their colonies).")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac_factory("alliance"))
async def removemember(inter: discord.Interaction, alliance: str, member: str):
    if not await member_exists(alliance, member):
        return await inter.response.send_message("❌ Member not found.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM members WHERE alliance=$1 AND member=$2",
            alliance, member
        )
    await inter.response.send_message(f"✅ Member **{member}** removed.", ephemeral=True)

@bot.tree.command(description="Remove a specific colony.")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac_factory("alliance"))
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
    async with bot.pool.acquire() as conn:
        res = await conn.execute(
            "DELETE FROM colonies WHERE alliance=$1 AND member=$2 AND sb=$3 AND x=$4 AND y=$5",
            alliance, member, sb, x, y
        )
    if res.endswith("0"):
        return await inter.response.send_message("❌ No such colony.", ephemeral=True)
    await inter.response.send_message(
        f"✅ Colony sb{sb} ({x},{y}) removed.", ephemeral=True
    )

@bot.tree.command(description="Rename a member.")
@app_commands.autocomplete(alliance=alliance_ac, old=member_ac_factory("alliance"))
async def renamemember(
    inter: discord.Interaction,
    alliance: str,
    old: str,
    new: str
):
    if not await member_exists(alliance, old):
        return await inter.response.send_message("❌ Original member not found.", ephemeral=True)
    if await member_exists(alliance, new):
        return await inter.response.send_message("❌ New name already exists.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute(
            "UPDATE members SET member=$1 WHERE alliance=$2 AND member=$3",
            new, alliance, old
        )
        await conn.execute(
            "UPDATE colonies SET member=$1 WHERE alliance=$2 AND member=$3",
            new, alliance, old
        )
    await inter.response.send_message(f"✅ Renamed **{old}** → **{new}**.", ephemeral=True)

# ---------------------------------------------------------------------------
# Run the bot
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    bot.run(TOKEN)
