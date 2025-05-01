# -*- coding: utf-8 -*-
"""
Galaxy Life Alliance Tracker Bot — PostgreSQL edition with attack command
=======================================================================
Adds /setalliance and /attack on top of the existing feature set.

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
MAX_MEMBERS  = 50
ADMIN_PASS   = "HAC#ER4LFElol567"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Set DISCORD_BOT_TOKEN")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Set DATABASE_URL")

TEST_GUILD = None
if tg := os.getenv("TEST_GUILD_ID"):
    try:
        TEST_GUILD = discord.Object(int(tg))
    except:
        print("Invalid TEST_GUILD_ID, ignoring")

# ---------------------------------------------------------------------------
# Bot & DB init
# ---------------------------------------------------------------------------
intents = discord.Intents.default()

class GalaxyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.pool: asyncpg.Pool | None = None

    async def setup_hook(self):
        # create pool
        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        async with self.pool.acquire() as conn:
            # alliances + members + settings
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

        # sync
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
# Helpers
# ---------------------------------------------------------------------------
async def alliance_exists(name:str) -> bool:
    return bool(await bot.pool.fetchval("SELECT 1 FROM alliances WHERE name=$1", name))

async def member_exists(alliance, member)->bool:
    return bool(await bot.pool.fetchval(
        "SELECT 1 FROM members WHERE alliance=$1 AND member=$2",
        alliance, member
    ))

async def colony_count(alliance, member)->int:
    return await bot.pool.fetchval(
        "SELECT COUNT(*) FROM colonies WHERE alliance=$1 AND member=$2",
        alliance, member
    )

async def all_alliances()->List[str]:
    rows = await bot.pool.fetch("SELECT name FROM alliances ORDER BY name")
    return [r["name"] for r in rows]

async def get_members_with_colonies(
    alliance:str
)->List[Tuple[str,int,List[Tuple[int,int,int]],int]]:
    # fetch members + main_sb
    mrows = await bot.pool.fetch(
        "SELECT member, COALESCE(main_sb,0) AS main_sb FROM members WHERE alliance=$1 ORDER BY member",
        alliance
    )
    # fetch colonies
    crows = await bot.pool.fetch("""
        SELECT member, starbase, x, y
          FROM colonies
         WHERE alliance=$1
         ORDER BY member, starbase DESC, x, y
    """, alliance)
    # group
    cmap = {r["member"]: [] for r in mrows}
    for c in crows:
        cmap[c["member"]].append((c["starbase"],c["x"],c["y"]))
    return [
        (r["member"], len(cmap[r["member"]]), cmap[r["member"]], r["main_sb"])
        for r in mrows
    ]

async def set_main_sb(alliance, member, sb):
    await bot.pool.execute(
        "UPDATE members SET main_sb=$1 WHERE alliance=$2 AND member=$3",
        sb, alliance, member
    )

async def set_active_alliance(guild_id:str, alliance:str):
    await bot.pool.execute("""
      INSERT INTO settings(guild_id,alliance) VALUES($1,$2)
       ON CONFLICT (guild_id) DO UPDATE SET alliance = EXCLUDED.alliance
    """, guild_id, alliance)

async def get_active_alliance(guild_id:str) -> str|None:
    return await bot.pool.fetchval(
        "SELECT alliance FROM settings WHERE guild_id=$1", guild_id
    )

# ---------------------------------------------------------------------------
# Autocomplete
# ---------------------------------------------------------------------------
async def alliance_ac(inter, current):
    low = current.lower()
    return [app_commands.Choice(n,n) for n in await all_alliances() if low in n.lower()][:25]

def member_ac(param):
    async def _ac(inter, cur):
        val = getattr(inter.namespace, param, None)
        if not val: return []
        rows = await bot.pool.fetch(
            "SELECT member FROM members WHERE alliance=$1 ORDER BY member", val
        )
        low=cur.lower()
        return [app_commands.Choice(r["member"],r["member"])
                for r in rows if low in r["member"].lower()][:25]
    return _ac

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@bot.tree.command(description="Create an alliance.")
async def addalliance(inter:discord.Interaction, name:str):
    if await alliance_exists(name):
        return await inter.response.send_message("❌ Already exists.", ephemeral=True)
    await bot.pool.execute("INSERT INTO alliances(name) VALUES($1)", name)
    await inter.response.send_message(f"✅ Alliance **{name}** created.", ephemeral=True)

@bot.tree.command(description="Add member (with main SB).")
@app_commands.autocomplete(alliance=alliance_ac)
async def addmember(
    inter:discord.Interaction,
    alliance:str,
    member:str,
    main_sb:app_commands.Range[int,1,9]
):
    if not await alliance_exists(alliance):
        return await inter.response.send_message("❌ No such alliance.", ephemeral=True)
    if await member_exists(alliance, member):
        return await inter.response.send_message("❌ Member exists.", ephemeral=True)
    await bot.pool.execute(
        "INSERT INTO members(alliance,member,main_sb) VALUES($1,$2,$3)",
        alliance,member,main_sb
    )
    await inter.response.send_message(f"✅ Added **{member}** (SB{main_sb}).", ephemeral=True)

@bot.tree.command(description="Add a colony.")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac("alliance"))
async def addcolony(
    inter:discord.Interaction,
    alliance:str,
    member:str,
    starbase:app_commands.Range[int,1,9],
    x:int, y:int
):
    if not await member_exists(alliance, member):
        return await inter.response.send_message("❌ Member not found.", ephemeral=True)
    if await colony_count(alliance, member)>=MAX_COLONIES:
        return await inter.response.send_message("❌ Max colonies reached.", ephemeral=True)
    await bot.pool.execute(
        "INSERT INTO colonies(alliance,member,starbase,x,y) VALUES($1,$2,$3,$4,$5)",
        alliance,member,starbase,x,y
    )
    await inter.response.send_message(f"✅ Colony SB{starbase} ({x},{y}) added.", ephemeral=True)

@bot.tree.command(description="Update a member’s main SB.")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac("alliance"))
async def setmainsb(
    inter:discord.Interaction,
    alliance:str,
    member:str,
    sb:app_commands.Range[int,1,9]
):
    if not await member_exists(alliance, member):
        return await inter.response.send_message("❌ Member not found.", ephemeral=True)
    await set_main_sb(alliance, member, sb)
    await inter.response.send_message(f"✅ **{member}**’s SB set to {sb}.", ephemeral=True)

@bot.tree.command(description="Show alliance members & colonies.")
@app_commands.autocomplete(alliance=alliance_ac)
async def show(inter:discord.Interaction, alliance:str):
    if not await alliance_exists(alliance):
        return await inter.response.send_message("❌ Alliance not found.", ephemeral=True)
    data = await get_members_with_colonies(alliance)
    embed = discord.Embed(
        title=f"{alliance} ({len(data)}/{MAX_MEMBERS} members)",
        color=discord.Color.blue()
    )
    total_cols=0
    for m, cnt, cols, msb in data:
        total_cols+=cnt
        header = f"{m} (SB{msb} — {cnt}/{MAX_COLONIES})"
        if not cols:
            embed.add_field(name=header, value="—", inline=False)
        else:
            lines = "\n".join(f"SB{sb} ({xx},{yy})" for sb,xx,yy in cols)
            embed.add_field(name=header, value=lines, inline=False)
    embed.set_footer(text=f"{total_cols} colonies discovered")
    await inter.response.send_message(embed=embed)

@bot.tree.command(description="List all alliances.")
async def list(inter:discord.Interaction):
    opts = await all_alliances()
    if not opts:
        return await inter.response.send_message("❌ No alliances.", ephemeral=True)
    await inter.response.send_message("\n".join(f"- {o}" for o in opts))

@bot.tree.command(description="Password-protected: set your active alliance.")
async def setalliance(
    inter:discord.Interaction,
    alliance:str,
    password:str
):
    if password!=ADMIN_PASS:
        return await inter.response.send_message("❌ Bad password.", ephemeral=True)
    if not await alliance_exists(alliance):
        return await inter.response.send_message("❌ No such alliance.", ephemeral=True)
    await set_active_alliance(str(inter.guild_id), alliance)
    await inter.response.send_message(f"✅ Active alliance set to **{alliance}**.", ephemeral=True)

@bot.tree.command(description="Attack an enemy alliance: show respawn timers.")
@app_commands.autocomplete(enemy=alliance_ac)
async def attack(
    inter:discord.Interaction,
    enemy:str,
    password:str
):
    if password!=ADMIN_PASS:
        return await inter.response.send_message("❌ Bad password.", ephemeral=True)
    # fetch active
    ours = await get_active_alliance(str(inter.guild_id))
    if not ours:
        return await inter.response.send_message("❌ No active alliance set.", ephemeral=True)
    if not await alliance_exists(enemy):
        return await inter.response.send_message("❌ Enemy alliance not found.", ephemeral=True)
    # sizes
    our_size   = len(await bot.pool.fetch(
        "SELECT 1 FROM members WHERE alliance=$1", ours
    ))
    enemy_size = len(await bot.pool.fetch(
        "SELECT 1 FROM members WHERE alliance=$1", enemy
    ))
    # formula
    Te = round(4 * max(enemy_size / our_size, 1))
    Ty = round(4 * max(our_size   / enemy_size, 1))
    em = discord.Embed(title="Respawn Timers", color=discord.Color.red())
    em.add_field(name="Our base respawn",   value=f"{Ty} hours", inline=True)
    em.add_field(name="Enemy base respawn", value=f"{Te} hours", inline=True)
    await inter.response.send_message(embed=em)

@bot.tree.command(description="Delete an alliance (admin only).")
@app_commands.autocomplete(alliance=alliance_ac)
async def reset(
    inter:discord.Interaction,
    alliance:str,
    password:str
):
    if password!=ADMIN_PASS:
        return await inter.response.send_message("❌ Bad password.", ephemeral=True)
    if not await alliance_exists(alliance):
        return await inter.response.send_message("❌ Alliance not found.", ephemeral=True)
    await bot.pool.execute("DELETE FROM alliances WHERE name=$1", alliance)
    await inter.response.send_message("✅ Alliance deleted.", ephemeral=True)

@bot.tree.command(description="Remove a member (& their colonies).")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac("alliance"))
async def removemember(inter, alliance:str, member:str):
    if not await member_exists(alliance, member):
        return await inter.response.send_message("❌ Member not found.", ephemeral=True)
    await bot.pool.execute(
        "DELETE FROM members WHERE alliance=$1 AND member=$2", alliance, member
    )
    await inter.response.send_message(f"✅ Removed **{member}**.", ephemeral=True)

@bot.tree.command(description="Remove a specific colony.")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac("alliance"))
async def removecolony(
    inter,
    alliance:str,
    member:str,
    starbase:app_commands.Range[int,1,9],
    x:int, y:int
):
    if not await member_exists(alliance, member):
        return await inter.response.send_message("❌ Member not found.", ephemeral=True)
    res = await bot.pool.execute(
      "DELETE FROM colonies WHERE alliance=$1 AND member=$2 AND starbase=$3 AND x=$4 AND y=$5",
      alliance,member,starbase,x,y
    )
    if res.endswith("0"):
        return await inter.response.send_message("❌ No such colony.", ephemeral=True)
    await inter.response.send_message(f"✅ Removed SB{starbase} ({x},{y}).", ephemeral=True)

@bot.tree.command(description="Rename a member (keeps colonies).")
@app_commands.autocomplete(alliance=alliance_ac, old=member_ac("alliance"))
async def renamemember(
    inter, alliance:str, old:str, new:str
):
    if not await member_exists(alliance, old):
        return await inter.response.send_message("❌ Original not found.", ephemeral=True)
    if await member_exists(alliance, new):
        return await inter.response.send_message("❌ New name taken.", ephemeral=True)
    async with bot.pool.acquire() as conn:
        async with conn.transaction():
            main_sb = await conn.fetchval(
                "SELECT main_sb FROM members WHERE alliance=$1 AND member=$2",
                alliance, old
            ) or 0
            await conn.execute(
                "INSERT INTO members(alliance,member,main_sb) VALUES($1,$2,$3)",
                alliance, new, main_sb
            )
            await conn.execute(
                "UPDATE colonies SET member=$1 WHERE alliance=$2 AND member=$3",
                new, alliance, old
            )
            await conn.execute(
                "DELETE FROM members WHERE alliance=$1 AND member=$2",
                alliance, old
            )
    await inter.response.send_message(f"✅ Renamed **{old}** ➔ **{new}**.", ephemeral=True)

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    bot.run(TOKEN)
