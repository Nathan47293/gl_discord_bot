# -*- coding: utf-8 -*-
"""
Galaxy Life Alliance Tracker Bot — PostgreSQL edition with attack timers
=======================================================================
Persistent data in Railway PostgreSQL. Supports:
- Alliances, members, colonies (with duplicate coords + starbase levels)
- Total colonies discovered footer
- Password-protected deletion and alliance-setting
- /attack command: calculates respawn timers for your and enemy bases

Requirements (requirements.txt):
    discord.py>=2.3
    asyncpg>=0.29

Environment variables:
    DISCORD_BOT_TOKEN – your Discord bot token
    DATABASE_URL      – Railway Postgres plugin URL
    TEST_GUILD_ID     – optional guild ID for instant slash-command sync
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
MAX_MEMBERS = 50
DELETE_PASSWORD = "HAC#ER4LFElol567"

# ---------------------------------------------------------------------------
# Configuration checks
# ---------------------------------------------------------------------------
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Set DISCORD_BOT_TOKEN env var")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Set DATABASE_URL env var")

TEST_GUILD: discord.Object | None = None
if tg := os.getenv("TEST_GUILD_ID"):
    try:
        TEST_GUILD = discord.Object(int(tg))
    except ValueError:
        print("WARNING: TEST_GUILD_ID is not an integer; ignoring")

# ---------------------------------------------------------------------------
# Bot definition & DB setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()

class GalaxyBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.pool: asyncpg.Pool | None = None

    async def setup_hook(self) -> None:
        # Initialize DB pool and schema
        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        await self._init_db()

        # Sync commands
        if TEST_GUILD:
            self.tree.clear_commands(guild=TEST_GUILD)
            self.tree.copy_global_to(guild=TEST_GUILD)
            await self.tree.sync(guild=TEST_GUILD)
            print(f"❇ Commands synced to test guild {TEST_GUILD.id}")
        else:
            await self.tree.sync()
            print("✅ Global commands synced")

    async def _init_db(self) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            # Alliances & members
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS alliances (
                  name TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS members (
                  alliance TEXT REFERENCES alliances(name) ON DELETE CASCADE,
                  member   TEXT,
                  PRIMARY KEY(alliance, member)
                );
            """)
            # Colonies with duplicate coords & starbase
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS colonies (
                  id        SERIAL PRIMARY KEY,
                  alliance  TEXT NOT NULL,
                  member    TEXT NOT NULL,
                  starbase  INT  NOT NULL,
                  x         INT  NOT NULL,
                  y         INT  NOT NULL,
                  FOREIGN KEY (alliance) REFERENCES alliances(name) ON DELETE CASCADE,
                  FOREIGN KEY (alliance, member)
                    REFERENCES members(alliance, member) ON DELETE CASCADE
                );
            """)
            # Settings table for storing your alliance per guild
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                  guild_id TEXT PRIMARY KEY,
                  alliance TEXT REFERENCES alliances(name) ON DELETE CASCADE
                );
            """)

bot = GalaxyBot()

# ---------------------------------------------------------------------------
# Database helper functions
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
    return [r[0] for r in rows]

async def get_members_with_colonies(
    alliance: str
) -> List[Tuple[str,int,List[Tuple[int,int,int]]]]:
    """
    Returns [(member, count, [(starbase,x,y),...]), ...],
    sorted by member name, colonies sorted by starbase DESC.
    """
    query = """
      SELECT m.member,
             COUNT(c.id) AS cnt,
             COALESCE(
               array_agg(c.starbase||','||c.x||','||c.y
                         ORDER BY c.starbase DESC, c.x, c.y)
               FILTER (WHERE c.id IS NOT NULL),
               '{}'
             ) AS data
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
        raw = r["data"]
        cols: List[Tuple[int,int,int]] = []
        for trio in raw:
            sb, xs, ys = trio.split(",")
            cols.append((int(sb), int(xs), int(ys)))
        result.append((r["member"], r["cnt"], cols))
    return result

async def get_own_alliance(guild_id: str) -> str | None:
    async with bot.pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT alliance FROM settings WHERE guild_id=$1", guild_id
        )

async def set_own_alliance(guild_id: str, alliance: str) -> None:
    async with bot.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO settings(guild_id, alliance)
            VALUES($1, $2)
            ON CONFLICT(guild_id) DO UPDATE SET alliance=EXCLUDED.alliance
        """, guild_id, alliance)

# ---------------------------------------------------------------------------
# Autocomplete helpers
# ---------------------------------------------------------------------------
async def alliance_ac(inter: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    names = await all_alliances()
    low = current.lower()
    return [app_commands.Choice(name=n, value=n) for n in names if low in n.lower()][:25]

def member_ac_factory(param: str):
    async def _ac(inter: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        alliance_val = getattr(inter.namespace, param, None)
        if not alliance_val:
            return []
        async with bot.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT member FROM members WHERE alliance=$1 ORDER BY member",
                alliance_val
            )
        low = current.lower()
        return [app_commands.Choice(name=r[0], value=r[0]) for r in rows if low in r[0].lower()][:25]
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

@bot.tree.command(description="Add a member to an alliance.")
@app_commands.autocomplete(alliance=alliance_ac)
async def addmember(inter: discord.Interaction, alliance: str, member: str):
    if not await alliance_exists(alliance):
        return await inter.response.send_message("❌ Alliance not found.", ephemeral=True)
    if await member_exists(alliance, member):
        return await inter.response.send_message("❌ Member exists.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO members(alliance, member) VALUES($1,$2)",
            alliance, member
        )
    await inter.response.send_message("✅ Member added.", ephemeral=True)

@bot.tree.command(description="Add a colony (starbase 1–9, then X, then Y).")
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
    await inter.response.send_message(f"✅ Colony SB{starbase} ({x},{y}) added.", ephemeral=True)

@bot.tree.command(description="Show an alliance’s members & colonies.")
@app_commands.autocomplete(alliance=alliance_ac)
async def show(inter: discord.Interaction, alliance: str):
    if not await alliance_exists(alliance):
        return await inter.response.send_message("❌ Alliance not found.", ephemeral=True)

    data = await get_members_with_colonies(alliance)
    total_colonies = sum(cnt for _,cnt,_ in data)

    embed = discord.Embed(
        title=f"{alliance} ({len(data)}/{MAX_MEMBERS} members)",
        color=discord.Color.blue()
    )
    if not data:
        embed.description = "No members recorded."
    else:
        for member, cnt, cols in data:
            if not cols:
                embed.add_field(name=f"{member} (0)", value="—", inline=False)
            else:
                lines = "\n".join(f"SB{sb} ({xx},{yy})" for sb,xx,yy in cols)
                embed.add_field(
                    name=f"{member} ({cnt}/{MAX_COLONIES})",
                    value=lines,
                    inline=False
                )
    embed.set_footer(text=f"{total_colonies} colonies discovered")
    await inter.response.send_message(embed=embed)

@bot.tree.command(name="list", description="List all alliances.")
async def list_alliances(inter: discord.Interaction):
    names = await all_alliances()
    if not names:
        return await inter.response.send_message("No alliances recorded.", ephemeral=True)
    await inter.response.send_message("\n".join(f"- {n}" for n in names))

@bot.tree.command(description="Delete an alliance (requires password).")
@app_commands.autocomplete(alliance=alliance_ac)
async def reset(
    inter: discord.Interaction,
    alliance: str,
    password: str
):
    if password != DELETE_PASSWORD:
        return await inter.response.send_message("❌ Invalid password.", ephemeral=True)
    if not await alliance_exists(alliance):
        return await inter.response.send_message("❌ Alliance not found.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        await conn.execute("DELETE FROM alliances WHERE name=$1", alliance)
    await inter.response.send_message(f"✅ Alliance **{alliance}** deleted.", ephemeral=True)

@bot.tree.command(description="Set your own alliance for this server (requires password).")
@app_commands.autocomplete(alliance=alliance_ac)
async def setalliance(
    inter: discord.Interaction,
    alliance: str,
    password: str
):
    if password != DELETE_PASSWORD:
        return await inter.response.send_message("❌ Invalid password.", ephemeral=True)
    if not await alliance_exists(alliance):
        return await inter.response.send_message("❌ Alliance not found.", ephemeral=True)
    await set_own_alliance(str(inter.guild_id), alliance)
    await inter.response.send_message(f"✅ This server's alliance set to **{alliance}**.", ephemeral=True)

@bot.tree.command(description="Attack an enemy alliance: show respawn timers.")
@app_commands.autocomplete(alliance=alliance_ac)
async def attack(
    inter: discord.Interaction,
    target: str
):
    """Calculates base respawn timers vs enemy alliance."""
    own = await get_own_alliance(str(inter.guild_id))
    if not own:
        return await inter.response.send_message(
            "❌ Please set your alliance first with /setalliance.", ephemeral=True
        )
    if not await alliance_exists(target):
        return await inter.response.send_message("❌ Enemy alliance not found.", ephemeral=True)

    # fetch sizes
    async with bot.pool.acquire() as conn:
        A = await conn.fetchval(
            "SELECT COUNT(*) FROM members WHERE alliance=$1", own
        )
        E = await conn.fetchval(
            "SELECT COUNT(*) FROM members WHERE alliance=$1", target
        )

    # compute timers
    ratio_enemy = max(E/A, 1)
    ratio_you   = max(A/E, 1)
    T_enemy = round(4 * ratio_enemy)
    T_you   = round(4 * ratio_you)

    embed = discord.Embed(
        title=f"Attack: **{own}** vs **{target}**",
        color=discord.Color.purple()
    )
    embed.add_field(
        name="🛡️ Our base respawn time",
        value=f"{T_you} hours",
        inline=True
    )
    embed.add_field(
        name="⚔️ Enemy base respawn time",
        value=f"{T_enemy} hours",
        inline=True
    )
    await inter.response.send_message(embed=embed)

# ---------------------------------------------------------------------------
# Run the bot
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    bot.run(TOKEN)
