# -*- coding: utf-8 -*-
"""
Galaxy Life Alliance Tracker Bot — PostgreSQL edition with Attack Command
=======================================================================

Requirements (in requirements.txt):
    discord.py>=2.3
    asyncpg>=0.29

Environment variables:
    DISCORD_BOT_TOKEN – your bot token
    DATABASE_URL      – your Railway PostgreSQL URL
    TEST_GUILD_ID     – optional guild ID for instant slash-command sync
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

# ────────────────────────────────────────────────────────────────────────────
# Configuration checks
# ────────────────────────────────────────────────────────────────────────────
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
    except ValueError:
        print("WARNING: invalid TEST_GUILD_ID, ignoring")

# ────────────────────────────────────────────────────────────────────────────
# Bot & Database initialization
# ────────────────────────────────────────────────────────────────────────────
intents = discord.Intents.default()

class GalaxyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.pool: asyncpg.Pool | None = None

    async def setup_hook(self):
        # open pool & init schema
        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        async with self.pool.acquire() as conn:
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

        # register commands
        if TEST_GUILD:
            # guild-only
            self.tree.clear_commands(guild=TEST_GUILD)
            self.tree.copy_global_to(guild=TEST_GUILD)
            await self.tree.sync(guild=TEST_GUILD)
            print(f"❇ Commands synced to test guild {TEST_GUILD.id}")
        else:
            await self.tree.sync()
            print("✅ Global commands synced")

bot = GalaxyBot()

# ────────────────────────────────────────────────────────────────────────────
# Database helper functions
# ────────────────────────────────────────────────────────────────────────────
async def alliance_exists(name: str) -> bool:
    return bool(await bot.pool.fetchval("SELECT 1 FROM alliances WHERE name=$1", name))

async def member_exists(alliance: str, member: str) -> bool:
    return bool(await bot.pool.fetchval(
        "SELECT 1 FROM members WHERE alliance=$1 AND member=$2",
        alliance, member
    ))

async def colony_count(alliance: str, member: str) -> int:
    return await bot.pool.fetchval(
        "SELECT COUNT(*) FROM colonies WHERE alliance=$1 AND member=$2",
        alliance, member
    )

async def all_alliances() -> List[str]:
    rows = await bot.pool.fetch("SELECT name FROM alliances ORDER BY name")
    return [r["name"] for r in rows]

async def get_members_with_colonies(alliance: str) -> List[Tuple[str, int, List[Tuple[int,int,int]], int]]:
    # returns list of (member, colony_count, [(sb,x,y)...], main_sb)
    mrows = await bot.pool.fetch(
        "SELECT member, COALESCE(main_sb,0) AS main_sb FROM members WHERE alliance=$1 ORDER BY member",
        alliance
    )
    crows = await bot.pool.fetch(
        "SELECT member, starbase, x, y "
        "FROM colonies WHERE alliance=$1 "
        "ORDER BY member, starbase DESC, x, y",
        alliance
    )
    cmap = {r["member"]: [] for r in mrows}
    for c in crows:
        cmap[c["member"]].append((c["starbase"], c["x"], c["y"]))
    return [
        (r["member"], len(cmap[r["member"]]), cmap[r["member"]], r["main_sb"])
        for r in mrows
    ]

async def set_main_sb(alliance: str, member: str, sb: int):
    await bot.pool.execute(
        "UPDATE members SET main_sb=$1 WHERE alliance=$2 AND member=$3",
        sb, alliance, member
    )

async def set_active_alliance(guild_id: str, alliance: str):
    await bot.pool.execute("""
      INSERT INTO settings(guild_id, alliance)
      VALUES($1,$2)
      ON CONFLICT (guild_id) DO UPDATE
        SET alliance = EXCLUDED.alliance
    """, guild_id, alliance)

async def get_active_alliance(guild_id: str) -> str|None:
    return await bot.pool.fetchval(
        "SELECT alliance FROM settings WHERE guild_id=$1", guild_id
    )

# ────────────────────────────────────────────────────────────────────────────
# Autocomplete helpers
# ────────────────────────────────────────────────────────────────────────────
async def alliance_ac(inter: discord.Interaction, current: str):
    low = current.lower()
    return [
        app_commands.Choice(name=n, value=n)
        for n in await all_alliances()
        if low in n.lower()
    ][:25]

def member_ac(param: str):
    async def _ac(inter: discord.Interaction, current: str):
        alliance_val = getattr(inter.namespace, param, None)
        if not alliance_val:
            return []
        rows = await bot.pool.fetch(
            "SELECT member FROM members WHERE alliance=$1 ORDER BY member",
            alliance_val
        )
        low = current.lower()
        return [
            app_commands.Choice(name=r["member"], value=r["member"])
            for r in rows
            if low in r["member"].lower()
        ][:25]
    return _ac

# ────────────────────────────────────────────────────────────────────────────
# Slash commands
# ────────────────────────────────────────────────────────────────────────────

@bot.tree.command(description="Create a new alliance.")
async def addalliance(inter: discord.Interaction, name: str):
    if await alliance_exists(name):
        return await inter.response.send_message("❌ Already exists.", ephemeral=True)
    await bot.pool.execute("INSERT INTO alliances(name) VALUES($1)", name)
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
    await bot.pool.execute(
        "INSERT INTO members(alliance,member,main_sb) VALUES($1,$2,$3)",
        alliance, member, main_sb
    )
    await inter.response.send_message(f"✅ Added **{member}** (SB{main_sb}).", ephemeral=True)

@bot.tree.command(description="Add a colony coordinate (max 11 per member).")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac("alliance"))
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
    await bot.pool.execute(
        "INSERT INTO colonies(alliance,member,starbase,x,y) VALUES($1,$2,$3,$4,$5)",
        alliance, member, starbase, x, y
    )
    await inter.response.send_message(f"✅ Colony SB{starbase} ({x},{y}) added.", ephemeral=True)

@bot.tree.command(description="Update a member’s main starbase level.")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac("alliance"))
async def setmainsb(
    inter: discord.Interaction,
    alliance: str,
    member: str,
    sb: app_commands.Range[int,1,9]
):
    if not await member_exists(alliance, member):
        return await inter.response.send_message("❌ Member not found.", ephemeral=True)
    await set_main_sb(alliance, member, sb)
    await inter.response.send_message(f"✅ **{member}**’s main SB set to {sb}.", ephemeral=True)

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
    for member, cnt, cols, msb in data:
        total_cols += cnt
        header = f"{member} (SB{msb} — {cnt}/{MAX_COLONIES})"
        if not cols:
            embed.add_field(name=header, value="—", inline=False)
        else:
            lines = "\n".join(f"SB{sb} ({xx},{yy})" for sb, xx, yy in cols)
            embed.add_field(name=header, value=lines, inline=False)

    embed.set_footer(text=f"{total_cols} colonies discovered")
    await inter.response.send_message(embed=embed)

@bot.tree.command(description="List all alliances.")
async def list(inter: discord.Interaction):
    opts = await all_alliances()
    if not opts:
        return await inter.response.send_message("❌ No alliances recorded.", ephemeral=True)
    await inter.response.send_message("\n".join(f"- {o}" for o in opts))

@bot.tree.command(description="Password-protected: set this guild’s alliance.")
async def setalliance(
    inter: discord.Interaction,
    alliance: str,
    password: str
):
    if password != ADMIN_PASS:
        return await inter.response.send_message("❌ Bad password.", ephemeral=True)
    if not await alliance_exists(alliance):
        return await inter.response.send_message("❌ Alliance not found.", ephemeral=True)
    await set_active_alliance(str(inter.guild_id), alliance)
    await inter.response.send_message(f"✅ Active alliance set to **{alliance}**.", ephemeral=True)

@bot.tree.command(description="Password-protected: delete an alliance.")
@app_commands.autocomplete(alliance=alliance_ac)
async def reset(
    inter: discord.Interaction,
    alliance: str,
    password: str
):
    if password != ADMIN_PASS:
        return await inter.response.send_message("❌ Bad password.", ephemeral=True)
    if not await alliance_exists(alliance):
        return await inter.response.send_message("❌ Alliance not found.", ephemeral=True)
    await bot.pool.execute("DELETE FROM alliances WHERE name=$1", alliance)
    await inter.response.send_message("✅ Alliance deleted.", ephemeral=True)

@bot.tree.command(description="Remove a member (and all their colonies).")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac("alliance"))
async def removemember(inter: discord.Interaction, alliance: str, member: str):
    if not await member_exists(alliance, member):
        return await inter.response.send_message("❌ Member not found.", ephemeral=True)
    await bot.pool.execute(
        "DELETE FROM members WHERE alliance=$1 AND member=$2",
        alliance, member
    )
    await inter.response.send_message(f"✅ Removed **{member}**.", ephemeral=True)

@bot.tree.command(description="Remove a specific colony.")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac("alliance"))
async def removecolony(
    inter: discord.Interaction,
    alliance: str,
    member: str,
    starbase: app_commands.Range[int,1,9],
    x: int, y: int
):
    if not await member_exists(alliance, member):
        return await inter.response.send_message("❌ Member not found.", ephemeral=True)
    res = await bot.pool.execute(
        "DELETE FROM colonies WHERE alliance=$1 AND member=$2 AND starbase=$3 AND x=$4 AND y=$5",
        alliance, member, starbase, x, y
    )
    if res.endswith("0"):
        return await inter.response.send_message("❌ No such colony.", ephemeral=True)
    await inter.response.send_message(f"✅ Removed SB{starbase} ({x},{y}).", ephemeral=True)

@bot.tree.command(description="Rename a member (keeps colonies).")
@app_commands.autocomplete(alliance=alliance_ac, old=member_ac("alliance"))
async def renamemember(
    inter: discord.Interaction,
    alliance: str,
    old: str,
    new: str
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
    await inter.response.send_message(f"✅ Renamed **{old}** to **{new}**.", ephemeral=True)

@bot.tree.command(description="Start a war against an enemy alliance and show war stats.")
@app_commands.autocomplete(target=alliance_ac)
async def attack(inter: discord.Interaction, target: str):
    # Ensure the guild has an active alliance
    own = await get_active_alliance(str(inter.guild_id))
    if not own:
        return await inter.response.send_message(
            "❌ Please set your alliance first with /setalliance.", ephemeral=True
        )

    # Validate the target alliance exists
    if not await alliance_exists(target):
        return await inter.response.send_message(
            "❌ Enemy alliance not found.", ephemeral=True
        )

    async with bot.pool.acquire() as conn:
        # Count members for cooldown calculations
        A = await conn.fetchval("SELECT COUNT(*) FROM members WHERE alliance=$1", own)
        E = await conn.fetchval("SELECT COUNT(*) FROM members WHERE alliance=$1", target)

        # Warpoints mapping by starbase level
        wp_map = {1: 100, 2: 200, 3: 300, 4: 400, 5: 600,
                  6: 1000, 7: 1500, 8: 2000, 9: 2500}

        # Fetch main SBs and colony starbases for both alliances
        main_enemy = await conn.fetch("SELECT main_sb FROM members WHERE alliance=$1", target)
        col_enemy  = await conn.fetch("SELECT starbase FROM colonies WHERE alliance=$1", target)
        main_own   = await conn.fetch("SELECT main_sb FROM members WHERE alliance=$1", own)
        col_own    = await conn.fetch("SELECT starbase FROM colonies WHERE alliance=$1", own)

    # Calculate total warpoints earned per raid
    own_wp   = sum(wp_map.get(r['main_sb'], 0) for r in main_enemy) + sum(wp_map.get(r['starbase'], 0) for r in col_enemy)
    enemy_wp = sum(wp_map.get(r['main_sb'], 0) for r in main_own)   + sum(wp_map.get(r['starbase'], 0) for r in col_own)

    # Calculate swapped cooldowns
    ratio_enemy = max(E / A, 1)
    ratio_you   = max(A / E, 1)
    T_enemy = round(4 * ratio_enemy)
    T_you   = round(4 * ratio_you)

    # Build the war embed
    embed = discord.Embed(
        title=f"War! **{own}** vs **{target}**",
        color=discord.Color.red()
    )
    embed.add_field(name="⚔️ Attacking cooldown", value=f"{T_enemy} hours", inline=True)
    embed.add_field(name="🛡️ Defending cooldown", value=f"{T_you} hours", inline=True)
    embed.add_field(name="⭐ WP/Raid", value=f"{own_wp:,}", inline=True)
    embed.add_field(name="★ Enemy WP/Raid", value=f"{enemy_wp:,}", inline=True)

    await inter.response.send_message(embed=embed)# ────────────────────────────────────────────────────────────────────────────
# Run
# ────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(TOKEN)
