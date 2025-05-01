# -*- coding: utf-8 -*-
"""
Galaxy Life Alliance Tracker Bot — **PostgreSQL edition**
========================================================
Persistent across every deploy **without volumes**.  Data is stored in the
free Railway **PostgreSQL** plugin; nothing writes to the container’s disk.

Key points
----------
* Same slash‑command API you already use.
* Tables auto‑create on first run — no manual migrations.
* Works in any Railway region (metal or non‑metal).

Add these to **requirements.txt** and commit:
```
discord.py>=2.3
asyncpg>=0.29
```

Environment variables needed:
* `DISCORD_BOT_TOKEN` – your Discord bot token (already set).
* `DATABASE_URL`      – auto‑created by Railway when you add the Postgres plugin.
* `TEST_GUILD_ID`     – *(optional)* guild ID for instant slash‑command sync.

Schema (all created automatically):
```
alliances(name TEXT PRIMARY KEY)
members(alliance TEXT, member TEXT, PRIMARY KEY (alliance, member))
colonies(alliance TEXT, member TEXT, x INT, y INT,
         PRIMARY KEY(alliance, member, x, y))
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
# Config & sanity checks
# ---------------------------------------------------------------------------
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Set the DISCORD_BOT_TOKEN env var.")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Add the PostgreSQL plugin so DATABASE_URL is set.")

TEST_GUILD = None
if "TEST_GUILD_ID" in os.environ:
    try:
        TEST_GUILD = discord.Object(int(os.environ["TEST_GUILD_ID"]))
    except ValueError:
        print("TEST_GUILD_ID must be an integer guild id")

# ---------------------------------------------------------------------------
# Bot with a global asyncpg pool
# ---------------------------------------------------------------------------
intents = discord.Intents.default()

class GalaxyBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.pool: asyncpg.Pool | None = None

    async def setup_hook(self) -> None:
        # open DB pool & init schema
        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        await self._init_db()

        # sync commands fast to test guild if provided
        if TEST_GUILD:
            self.tree.copy_global_to(guild=TEST_GUILD)
            await self.tree.sync(guild=TEST_GUILD)
            print(f"Commands synced to test guild {TEST_GUILD.id}")
        else:
            await self.tree.sync()
            print("Global commands synced (can take up to an hour first time)")

    async def _init_db(self):
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
# Helper / DB utility functions
# ---------------------------------------------------------------------------
async def alliance_exists(name: str) -> bool:
    async with bot.pool.acquire() as conn:  # type: ignore
        return await conn.fetchval("SELECT 1 FROM alliances WHERE name=$1", name) is not None

async def member_exists(alliance: str, member: str) -> bool:
    async with bot.pool.acquire() as conn:  # type: ignore
        return (
            await conn.fetchval(
                "SELECT 1 FROM members WHERE alliance=$1 AND member=$2", alliance, member
            )
        ) is not None

async def colony_count(alliance: str, member: str) -> int:
    async with bot.pool.acquire() as conn:  # type: ignore
        return await conn.fetchval(
            "SELECT COUNT(*) FROM colonies WHERE alliance=$1 AND member=$2", alliance, member
        )

async def all_alliances() -> List[str]:
    async with bot.pool.acquire() as conn:  # type: ignore
        rows = await conn.fetch("SELECT name FROM alliances ORDER BY name")
    return [r[0] for r in rows]

async def get_members_with_colonies(alliance: str) -> List[Tuple[str, int, List[Tuple[int, int]]]]:
    query = """
        SELECT m.member,
               COUNT(c.x) AS ncol,
               COALESCE(array_agg(c.x || ',' || c.y ORDER BY c.x, c.y)
                        FILTER (WHERE c.x IS NOT NULL), '{}') AS coords
        FROM members m
        LEFT JOIN colonies c
               ON c.alliance = m.alliance AND c.member = m.member
        WHERE m.alliance = $1
        GROUP BY m.member
        ORDER BY m.member;
    """
    async with bot.pool.acquire() as conn:  # type: ignore
        rows = await conn.fetch(query, alliance)
    results: List[Tuple[str, int, List[Tuple[int, int]]]] = []
    for r in rows:
        coords_list = [tuple(map(int, s.split(','))) for s in r[2]] if r[2] else []
        results.append((r[0], r[1], coords_list))
    return results

# ---------------------------------------------------------------------------
# Autocomplete helpers
# ---------------------------------------------------------------------------
async def alliance_ac(inter: discord.Interaction, current: str):
    names = await all_alliances()
    cur = current.lower()
    return [app_commands.Choice(name=n, value=n) for n in names if cur in n.lower()][:25]


def member_ac_factory(param_alliance: str):
    async def _ac(inter: discord.Interaction, current: str):
        alliance_val = getattr(inter.namespace, param_alliance, None)
        if not alliance_val:
            return []
        async with bot.pool.acquire() as conn:  # type: ignore
            rows = await conn.fetch(
                "SELECT member FROM members WHERE alliance=$1 ORDER BY member", alliance_val
            )
        cur = current.lower()
        return [
            app_commands.Choice(name=r[0], value=r[0]) for r in rows if cur in r[0].lower()
        ][:25]

    return _ac

# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------
@bot.tree.command(description="Create a new alliance entry.")
@app_commands.describe(name="Alliance name")
async def addalliance(inter: discord.Interaction, name: str):
    if await alliance_exists(name):
        await inter.response.send_message("Alliance already exists.", ephemeral=True)
        return
    async with bot.pool.acquire() as conn:  # type: ignore
        await conn.execute("INSERT INTO alliances(name) VALUES ($1)", name)
    await inter.response.send_message(f"Alliance **{name}** registered!", ephemeral=True)


@bot.tree.command(description="Add a member to an alliance.")
@app_commands.autocomplete(alliance=alliance_ac)
@app_commands.describe(alliance="Alliance name", member="Member name")
async def addmember(inter: discord.Interaction, alliance: str, member: str):
    if not await alliance_exists(alliance):
        await inter.response.send_message("Alliance not found.", ephemeral=True)
        return
    if await member_exists(alliance, member):
        await inter.response.send_message("Member already exists.", ephemeral=True)
        return
    async with bot.pool.acquire() as conn:  # type: ignore
        await conn.execute(
            "INSERT INTO members(alliance, member) VALUES ($1, $2)", alliance, member
        )
    await inter.response.send_message("Member added.", ephemeral=True)


@bot.tree.command(description="Add a colony coordinate (max 11 per member).")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac_factory("alliance"))
@app_commands.describe(alliance="Alliance", member="Member", x="X", y="Y")
async def addcolony(
    inter: discord.Interaction,
    alliance: str,
    member: str,
    x: int,
    y: int,
):
    if not await member_exists(alliance, member):
        await inter.response.send_message("Member not found.", ephemeral=True)
        return
    if await colony_count(alliance, member) >= MAX_COLONIES:
        await inter.response.send_message("Max 11 colonies reached.", ephemeral=True)
        return
    async with bot.pool.acquire() as conn:  # type: ignore
        await conn.execute(
            "INSERT INTO colonies(alliance, member, x, y) VALUES ($1,$2,$3,$4)",
            alliance,
            member,
            x,
            y,
        )
    await inter.response.send_message("Colony added.", ephemeral=True)


@bot.tree.command(description="Show an alliance’s members & colonies.")
@app_commands.autocomplete(alliance=alliance_ac)
async def show(inter: discord.Interaction, alliance: str):
    if not await alliance_exists(alliance):
        await inter.response.send_message("Alliance not found.", ephemeral=True)
        return
    members = await get_members_with_colonies(alliance)
    embed = discord.Embed(title=alliance, color=discord.Color.blue())
    if not members:
        embed.description = "No members recorded."
    else:
        for name, count, coords in members:
            coord_str = ", ".join(f"{x},{y}" for x, y in coords) or "None"
            embed.add_field(
                name=f"{name} ({count}/{MAX_COLONIES})",
                value=coord_str,
                inline=False,
            )
    await inter.response.send_message(embed=embed, ephemeral=False)


@bot.tree.command(description="List all alliances.")
async def list(inter: discord.Interaction):
    names = await all_alliances()
    if not names:
        await inter.response.send_message("No alliances recorded.", ephemeral=True)
        return
    await inter.response.send_message(
        "\n".join(f"- {n}" for n in names), ephemeral=False
    )


@bot.tree.command(description="Delete an alliance (admin only).")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.autocomplete(alliance=alliance_ac)
async def reset(inter: discord.Interaction, alliance: str):
    if not await alliance_exists(alliance):
        await inter.response.send_message("Alliance not found.", ephemeral=True)
        return
    async with bot.pool.acquire() as conn:  # type: ignore
        await conn.execute("DELETE FROM alliances WHERE name=$1", alliance)
    await inter.response.send
    await inter.response.send_message("Alliance deleted.", ephemeral=True)

@reset.error
async def reset_error(inter: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await inter.response.send_message("Administrator permission required.", ephemeral=True)
    else:
        raise error

# ---------------------------------------------------------------------------
# Run bot
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    bot.run(TOKEN)
