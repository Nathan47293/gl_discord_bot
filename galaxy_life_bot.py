# -*- coding: utf-8 -*-
"""
Galaxy Life Alliance Tracker Bot — **PostgreSQL edition**
========================================================
Persistent across every deploy **without volumes**. Data is stored in the
free Railway **PostgreSQL** plugin; nothing writes to the container’s disk.

Key points
----------
* Same slash-command API you already use.
* Tables auto-create on first run — no manual migrations.
* Works in any Railway region (metal or non-metal).

Add these to **requirements.txt** and commit:
```
discord.py>=2.3
asyncpg>=0.29
```

Environment variables needed:
* `DISCORD_BOT_TOKEN` – your Discord bot token.
* `DATABASE_URL`      – set by the Railway PostgreSQL plugin.
* `TEST_GUILD_ID`     – *(optional)* guild ID for instant slash-command sync.

Schema (auto-created):
```
alliances(name TEXT PRIMARY KEY)
members(alliance TEXT, member TEXT, PRIMARY KEY(alliance, member))
colonies(alliance TEXT, member TEXT, x INT, y INT, PRIMARY KEY(alliance, member, x, y))
```
Each member is limited to **11 colonies**.
"""

from __future__ import annotations
import os
from typing import List, Tuple
import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

MAX_COLONIES = 11

# ---------------------------------------------------------------------------
# Configuration checks
# ---------------------------------------------------------------------------
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Set the DISCORD_BOT_TOKEN env var.")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Add the PostgreSQL plugin so DATABASE_URL is set.")

TEST_GUILD: discord.Object | None = None
if "TEST_GUILD_ID" in os.environ:
    try:
        TEST_GUILD = discord.Object(int(os.environ["TEST_GUILD_ID"]))
    except ValueError:
        print("TEST_GUILD_ID must be an integer guild id")

# ---------------------------------------------------------------------------
# Bot definition with asyncpg pool
# ---------------------------------------------------------------------------
intents = discord.Intents.default()

async def setup_hook(self) -> None:
        # Initialize DB …
        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        await self._init_db()

        if TEST_GUILD:
            # 1) Delete all global commands
            self.tree.clear_commands(guild=None)
            # 2) Delete all existing guild commands
            self.tree.clear_commands(guild=TEST_GUILD)
            # 3) Register *only* your code’s commands in that guild
            self.tree.copy_global_to(guild=TEST_GUILD)
            # 4) Sync to push them live
            await self.tree.sync(guild=TEST_GUILD)
            print(f"❇ Cleared GLOBAL & GUILD commands, re-synced to guild {TEST_GUILD.id}")
        else:
            # In production you can leave this empty (or sync globals if you want)
            print("Running in production: no test-guild sync")

    async def _init_db(self) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
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
                    x        INT,
                    y        INT,
                    PRIMARY KEY(alliance, member, x, y),
                    FOREIGN KEY (alliance, member)
                        REFERENCES members(alliance, member)
                        ON DELETE CASCADE
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
            "SELECT 1 FROM members WHERE alliance=$1 AND member=$2", alliance, member
        ) is not None

async def colony_count(alliance: str, member: str) -> int:
    async with bot.pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM colonies WHERE alliance=$1 AND member=$2", alliance, member
        )

async def all_alliances() -> List[str]:
    async with bot.pool.acquire() as conn:
        rows = await conn.fetch("SELECT name FROM alliances ORDER BY name")
    return [r[0] for r in rows]

async def get_members_with_colonies(alliance: str) -> List[Tuple[str, int, List[Tuple[int, int]]]]:
    query = """
        SELECT m.member,
               COUNT(c.x)  AS ncol,
               COALESCE(array_agg(c.x || ',' || c.y ORDER BY c.x, c.y)
                        FILTER (WHERE c.x IS NOT NULL), '{}') AS coords
        FROM members m
        LEFT JOIN colonies c ON c.alliance = m.alliance AND c.member = m.member
        WHERE m.alliance = $1
        GROUP BY m.member
        ORDER BY m.member;
    """
    async with bot.pool.acquire() as conn:
        rows = await conn.fetch(query, alliance)
    result: List[Tuple[str, int, List[Tuple[int, int]]]] = []
    for r in rows:
        coords = [tuple(map(int, s.split(','))) for s in r[2]] if r[2] else []
        result.append((r[0], r[1], coords))
    return result

# ---------------------------------------------------------------------------
# Autocomplete helpers
# ---------------------------------------------------------------------------
async def alliance_ac(inter: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    names = await all_alliances()
    cur = current.lower()
    return [app_commands.Choice(name=n, value=n) for n in names if cur in n.lower()][:25]


def member_ac_factory(param_alliance: str):
    async def _ac(inter: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        alliance_val = getattr(inter.namespace, param_alliance, None)
        if not alliance_val:
            return []
        async with bot.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT member FROM members WHERE alliance=$1 ORDER BY member", alliance_val
            )
        cur = current.lower()
        return [app_commands.Choice(name=r[0], value=r[0]) for r in rows if cur in r[0].lower()][:25]
    return _ac

# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------
@bot.tree.command(description="Create a new alliance entry.")
@app_commands.describe(name="Alliance name")
async def addalliance(inter: discord.Interaction, name: str):
    if await alliance_exists(name):
        return await inter.response.send_message("Alliance already exists.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute("INSERT INTO alliances(name) VALUES($1)", name)
    await inter.response.send_message(f"Alliance **{name}** registered!", ephemeral=True)

@bot.tree.command(description="Add a member to an alliance.")
@app_commands.autocomplete(alliance=alliance_ac)
@app_commands.describe(alliance="Alliance name", member="Member name")
async def addmember(inter: discord.Interaction, alliance: str, member: str):
    if not await alliance_exists(alliance):
        return await inter.response.send_message("Alliance not found.", ephemeral=True)
    if await member_exists(alliance, member):
        return await inter.response.send_message("Member already exists.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute("INSERT INTO members(alliance, member) VALUES($1,$2)", alliance, member)
    await inter.response.send_message("Member added.", ephemeral=True)

@bot.tree.command(description="Add a colony coordinate (max 11 per member)." )
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac_factory("alliance"))
@app_commands.describe(alliance="Alliance", member="Member", x="X coord", y="Y coord")
async def addcolony(inter: discord.Interaction, alliance: str, member: str, x: int, y: int):
    if not await member_exists(alliance, member):
        return await inter.response.send_message("Member not found.", ephemeral=True)
    if await colony_count(alliance, member) >= MAX_COLONIES:
        return await inter.response.send_message("Max colonies reached.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute("INSERT INTO colonies(alliance, member, x, y) VALUES($1,$2,$3,$4)", alliance, member, x, y)
    await inter.response.send_message("Colony added.", ephemeral=True)

@bot.tree.command(description="Show an alliance’s members & colonies.")
@app_commands.autocomplete(alliance=alliance_ac)
async def show(inter: discord.Interaction, alliance: str):
    # Verify alliance exists
    if not await alliance_exists(alliance):
        return await inter.response.send_message("Alliance not found.", ephemeral=True)

    members = await get_members_with_colonies(alliance)
    total_members = len(members)

    # Embed title shows member count / 50
    embed = discord.Embed(
        title=f"{alliance} ({total_members}/50 members)",
        color=discord.Color.blue()
    )

    if not members:
        embed.description = "No members recorded."
    else:
        for name, count, coords in members:
            coord_str = ", ".join(f"{x},{y}" for x, y in coords) or "None"
            embed.add_field(
                name=f"{name} ({count}/{MAX_COLONIES})",
                value=coord_str,
                inline=False
            )

    await inter.response.send_message(embed=embed, ephemeral=False)

@bot.tree.command(description="List all alliances.")
async def list(inter: discord.Interaction):
    names = await all_alliances()
    if not names:
        return await inter.response.send_message("No alliances recorded.", ephemeral=True)
    await inter.response.send_message("\n".join(f"- {n}" for n in names), ephemeral=False)

@bot.tree.command(description="Delete an alliance (admin only)." )
@app_commands.autocomplete(alliance=alliance_ac)
async def reset(inter: discord.Interaction, alliance: str):
    if not await alliance_exists(alliance):
        return await inter.response.send_message("Alliance not found.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute("DELETE FROM alliances WHERE name=$1", alliance)
    await inter.response.send_message("Alliance deleted.", ephemeral=True)

@bot.tree.command(description="Remove a member (and all their colonies)." )
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac_factory("alliance"))
async def removemember(inter: discord.Interaction, alliance: str, member: str):
    if not await member_exists(alliance, member):
        return await inter.response.send_message("Member not found.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute("DELETE FROM members WHERE alliance=$1 AND member=$2", alliance, member)
    await inter.response.send_message(f"Member **{member}** removed.", ephemeral=True)

@bot.tree.command(description="Remove a specific colony.")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac_factory("alliance"))
async def removecolony(inter: discord.Interaction, alliance: str, member: str, x: int, y: int):
    if not await member_exists(alliance, member):
        return await inter.response.send_message("Member not found.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        res = await conn.execute(
            "DELETE FROM colonies WHERE alliance=$1 AND member=$2 AND x=$3 AND y=$4",
            alliance, member, x, y
        )
    if res.endswith("0"):
        return await inter.response.send_message("No such colony.", ephemeral=True)
    await inter.response.send_message(f"Colony `{x},{y}` removed.", ephemeral=True)

@bot.tree.command(description="Rename a member.")
@app_commands.autocomplete(alliance=alliance_ac, old=member_ac_factory("alliance"))
async def renamemember(inter: discord.Interaction, alliance: str, old: str, new: str):
    if not await member_exists(alliance, old):
        return await inter.response.send_message("Original member not found.", ephemeral=True)
    if await member_exists(alliance, new):
        return await inter.response.send_message("New name already exists.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute(
            "UPDATE members SET member=$1 WHERE alliance=$2 AND member=$3",
            new, alliance, old
        )
        await conn.execute(
            "UPDATE colonies SET member=$1 WHERE alliance=$2 AND member=$3",
            new, alliance, old
        )
    await inter.response.send_message(f"Member **{old}** renamed to **{new}**.", ephemeral=True)

# ---------------------------------------------------------------------------
# Run the bot
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    bot.run(TOKEN)
