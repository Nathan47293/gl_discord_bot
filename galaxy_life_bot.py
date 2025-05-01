# -*- coding: utf-8 -*-
"""
Galaxy Life Alliance Tracker Bot — PostgreSQL edition with duplicate coords
=========================================================================
Now allows multiple colonies at the same (x,y) for a given member by using
an auto-incrementing ID as the primary key, and fixes renaming members.

Requirements (requirements.txt):
    discord.py>=2.3
    asyncpg>=0.29

Env vars:
    DISCORD_BOT_TOKEN – your bot token
    DATABASE_URL      – Railway Postgres plugin URL
    TEST_GUILD_ID     – optional guild ID for instant slash sync
"""

from __future__ import annotations
import os
from typing import List, Tuple

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

MAX_COLONIES = 11
MAX_MEMBERS = 50

# ---------------------------------------------------------------------------
# Configuration checks
# ---------------------------------------------------------------------------
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Set the DISCORD_BOT_TOKEN env var.")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Set the DATABASE_URL env var (Postgres plugin).")

TEST_GUILD: discord.Object | None = None
if tg := os.getenv("TEST_GUILD_ID"):
    try:
        TEST_GUILD = discord.Object(int(tg))
    except ValueError:
        print("WARNING: TEST_GUILD_ID is not an integer; ignoring.")

# ---------------------------------------------------------------------------
# Bot definition & DB setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()

class GalaxyBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.pool: asyncpg.Pool | None = None

    async def setup_hook(self) -> None:
        # Open DB pool & init schema
        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        async with self.pool.acquire() as conn:
            # alliances & members
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
            """)
            # colonies with SERIAL id (allows duplicate coords)
            await conn.execute("""
                DROP TABLE IF EXISTS colonies;
                CREATE TABLE colonies (
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
            """)

        # sync commands
        if TEST_GUILD:
            self.tree.clear_commands(guild=TEST_GUILD)
            self.tree.copy_global_to(guild=TEST_GUILD)
            await self.tree.sync(guild=TEST_GUILD)
            print(f"❇ Commands synced to test guild {TEST_GUILD.id}")
        else:
            await self.tree.sync()
            print("✅ Global commands synced")

bot = GalaxyBot()

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
async def alliance_exists(name: str) -> bool:
    async with bot.pool.acquire() as conn:
        return bool(await conn.fetchval(
            "SELECT 1 FROM alliances WHERE name=$1", name
        ))

async def member_exists(alliance: str, member: str) -> bool:
    async with bot.pool.acquire() as conn:
        return bool(await conn.fetchval(
            "SELECT 1 FROM members WHERE alliance=$1 AND member=$2",
            alliance, member
        ))

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

async def get_members_with_colonies(
    alliance: str
) -> List[Tuple[str,int,List[Tuple[int,int,int]],int]]:
    """
    Returns [(member, count, [(sb,x,y)...], main_sb), ...]
    """
    # fetch members + main_sb
    async with bot.pool.acquire() as conn:
        mrows = await conn.fetch(
            "SELECT member, COALESCE(main_sb,0) AS main_sb FROM members WHERE alliance=$1 ORDER BY member",
            alliance
        )
    # fetch colonies sorted
    async with bot.pool.acquire() as conn:
        crows = await conn.fetch("""
            SELECT member, starbase, x, y
              FROM colonies
             WHERE alliance=$1
             ORDER BY member, starbase DESC, x, y
        """, alliance)
    # group by member
    coord_map = {r["member"]: [] for r in mrows}
    for c in crows:
        coord_map[c["member"]].append((c["starbase"], c["x"], c["y"]))

    return [
        (r["member"], len(coord_map[r["member"]]), coord_map[r["member"]], r["main_sb"])
        for r in mrows
    ]

async def set_main_sb(alliance: str, member: str, sb: int) -> None:
    async with bot.pool.acquire() as conn:
        await conn.execute(
            "UPDATE members SET main_sb=$1 WHERE alliance=$2 AND member=$3",
            sb, alliance, member
        )

# ---------------------------------------------------------------------------
# Autocomplete
# ---------------------------------------------------------------------------
async def alliance_ac(inter: discord.Interaction, current: str):
    opts = await all_alliances()
    low = current.lower()
    return [app_commands.Choice(name=o, value=o) for o in opts if low in o.lower()][:25]

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
        low = current.lower()
        return [app_commands.Choice(name=r["member"], value=r["member"])
                for r in rows if low in r["member"].lower()][:25]
    return _ac

# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------
@bot.tree.command(description="Create a new alliance.")
async def addalliance(inter: discord.Interaction, name: str):
    if await alliance_exists(name):
        return await inter.response.send_message("❌ Already exists.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute("INSERT INTO alliances(name) VALUES($1)", name)
    await inter.response.send_message(f"✅ Alliance **{name}** created.", ephemeral=True)

@bot.tree.command(description="Add a member to an alliance (with main SB).")
@app_commands.autocomplete(alliance=alliance_ac)
async def addmember(
    inter: discord.Interaction,
    alliance: str,
    member: str,
    main_sb: app_commands.Range[int,1,9]
):
    if not await alliance_exists(alliance):
        return await inter.response.send_message("❌ Alliance not found.", ephemeral=True)
    if await member_exists(alliance, member):
        return await inter.response.send_message("❌ Member exists.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO members(alliance,member,main_sb) VALUES($1,$2,$3)",
            alliance, member, main_sb
        )
    await inter.response.send_message(
        f"✅ Added **{member}** (SB{main_sb}).", ephemeral=True
    )

@bot.tree.command(description="Add a colony (starbase 1–9, then x, then y).")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac_factory("alliance"))
async def addcolony(
    inter: discord.Interaction,
    alliance: str,
    member: str,
    starbase: app_commands.Range[int,1,9],
    x: int,
    y: int
):
    if not await member_exists(alliance, member):
        return await inter.response.send_message("❌ Member not found.", ephemeral=True)
    if await colony_count(alliance, member) >= MAX_COLONIES:
        return await inter.response.send_message("❌ Max colonies reached.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO colonies(alliance,member,starbase,x,y) VALUES($1,$2,$3,$4,$5)",
            alliance, member, starbase, x, y
        )
    await inter.response.send_message(
        f"✅ Colony SB{starbase} ({x},{y}) added.", ephemeral=True
    )

@bot.tree.command(description="Set or update a member’s main SB.")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac_factory("alliance"))
async def setmainsb(
    inter: discord.Interaction,
    alliance: str,
    member: str,
    sb: app_commands.Range[int,1,9]
):
    if not await member_exists(alliance, member):
        return await inter.response.send_message("❌ Member not found.", ephemeral=True)
    await set_main_sb(alliance, member, sb)
    await inter.response.send_message(f"✅ **{member}**’s SB updated to {sb}.", ephemeral=True)

@bot.tree.command(description="Show an alliance’s members & colonies.")
@app_commands.autocomplete(alliance=alliance_ac)
async def show(inter: discord.Interaction, alliance: str):
    if not await alliance_exists(alliance):
        return await inter.response.send_message("❌ Alliance not found.", ephemeral=True)
    data = await get_members_with_colonies(alliance)
    embed = discord.Embed(
        title=f"{alliance} ({len(data)}/{MAX_MEMBERS} members)",
        color=discord.Color.blue()
    )
    total_cols = 0
    for member, cnt, cols, main_sb in data:
        total_cols += cnt
        header = f"{member} (SB{main_sb} — {cnt}/{MAX_COLONIES})"
        if not cols:
            embed.add_field(name=header, value="—", inline=False)
        else:
            lines = "\n".join(f"SB{sb} ({xx},{yy})" for sb,xx,yy in cols)
            embed.add_field(name=header, value=lines, inline=False)
    embed.set_footer(text=f"{total_cols} colonies discovered")
    await inter.response.send_message(embed=embed)

@bot.tree.command(description="List all alliances.")
async def list(inter: discord.Interaction):
    opts = await all_alliances()
    if not opts:
        return await inter.response.send_message("❌ No alliances recorded.", ephemeral=True)
    await inter.response.send_message("\n".join(f"- {o}" for o in opts))

@bot.tree.command(description="Delete an alliance (admin only).")
@app_commands.autocomplete(alliance=alliance_ac)
async def reset(inter: discord.Interaction, alliance: str):
    if not await alliance_exists(alliance):
        return await inter.response.send_message("❌ Alliance not found.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute("DELETE FROM alliances WHERE name=$1", alliance)
    await inter.response.send_message("✅ Alliance deleted.", ephemeral=True)

@bot.tree.command(description="Remove a member (& all their colonies).")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac_factory("alliance"))
async def removemember(inter: discord.Interaction, alliance: str, member: str):
    if not await member_exists(alliance, member):
        return await inter.response.send_message("❌ Member not found.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM members WHERE alliance=$1 AND member=$2",
            alliance, member
        )
    await inter.response.send_message(f"✅ Removed **{member}**.", ephemeral=True)

@bot.tree.command(description="Remove a specific colony.")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac_factory("alliance"))
async def removecolony(
    inter: discord.Interaction,
    alliance: str,
    member: str,
    starbase: app_commands.Range[int,1,9],
    x: int,
    y: int
):
    if not await member_exists(alliance, member):
        return await inter.response.send_message("❌ Member not found.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        res = await conn.execute(
            "DELETE FROM colonies WHERE alliance=$1 AND member=$2 AND starbase=$3 AND x=$4 AND y=$5",
            alliance, member, starbase, x, y
        )
    if res.endswith("0"):
        return await inter.response.send_message("❌ No such colony.", ephemeral=True)
    await inter.response.send_message(
        f"✅ Removed SB{starbase} ({x},{y}) for **{member}**.", ephemeral=True
    )

@bot.tree.command(description="Rename a member (preserves colonies).")
@app_commands.autocomplete(alliance=alliance_ac, old=member_ac_factory("alliance"))
async def renamemember(
    inter: discord.Interaction,
    alliance: str,
    old: str,
    new: str
):
    """Atomically reparent colonies then rename."""
    if not await member_exists(alliance, old):
        return await inter.response.send_message("❌ Original member not found.", ephemeral=True)
    if await member_exists(alliance, new):
        return await inter.response.send_message("❌ New member name already exists.", ephemeral=True)

    async with bot.pool.acquire() as conn:
        async with conn.transaction():
            # fetch old main_sb
            main_sb = await conn.fetchval(
                "SELECT main_sb FROM members WHERE alliance=$1 AND member=$2",
                alliance, old
            ) or 0
            # insert new member row
            await conn.execute(
                "INSERT INTO members(alliance,member,main_sb) VALUES($1,$2,$3)",
                alliance, new, main_sb
            )
            # reparent colonies
            await conn.execute(
                "UPDATE colonies SET member=$1 WHERE alliance=$2 AND member=$3",
                new, alliance, old
            )
            # delete old member
            await conn.execute(
                "DELETE FROM members WHERE alliance=$1 AND member=$2",
                alliance, old
            )

    await inter.response.send_message(f"✅ Renamed **{old}** ➔ **{new}**.", ephemeral=True)

# ---------------------------------------------------------------------------
# Run the bot
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    bot.run(TOKEN)
