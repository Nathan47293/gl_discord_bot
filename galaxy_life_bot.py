# -*- coding: utf-8 -*-
"""
Galaxy Life Alliance Tracker Bot — PostgreSQL edition with War & Attack UI
=======================================================================
"""
from __future__ import annotations
import os
import datetime
import math

import asyncpg
import discord
from discord import app_commands, ButtonStyle
from discord.ext import commands
from discord import ui

# Constants
token_env = "DISCORD_BOT_TOKEN"
db_env = "DATABASE_URL"
ADMIN_PASS = "HAC#ER4LFElol567"
MAX_COLONIES = 11
MAX_MEMBERS = 50

# Intents
intents = discord.Intents.default()

class GalaxyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.pool: asyncpg.Pool | None = None

    async def setup_hook(self):
        # Open pool & init schema
        DATABASE_URL = os.getenv(db_env)
        if not DATABASE_URL:
            raise RuntimeError(f"Set {db_env}")
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
            -- War persistence tables
            CREATE TABLE IF NOT EXISTS wars (
              guild_id       TEXT PRIMARY KEY
                              REFERENCES settings(guild_id)
                              ON DELETE CASCADE,
              enemy_alliance TEXT NOT NULL
                              REFERENCES alliances(name),
              start_time     TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS war_attacks (
              guild_id    TEXT NOT NULL
                          REFERENCES wars(guild_id)
                          ON DELETE CASCADE,
              member      TEXT NOT NULL,
              last_attack TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              PRIMARY KEY (guild_id, member)
            );
            """)
        # Register commands
        TEST_GUILD = None
        if tg := os.getenv("TEST_GUILD_ID"):
            try:
                TEST_GUILD = discord.Object(int(tg))
            except ValueError:
                pass
        if TEST_GUILD:
            self.tree.clear_commands(guild=TEST_GUILD)
            self.tree.copy_global_to(guild=TEST_GUILD)
            await self.tree.sync(guild=TEST_GUILD)
            print(f"❇ Commands synced to test guild {TEST_GUILD.id}")
        else:
            await self.tree.sync()
            print("✅ Global commands synced")

bot = GalaxyBot()

# -- Database helpers ---------------------------------------------------------
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

async def all_alliances() -> list[str]:
    rows = await bot.pool.fetch("SELECT name FROM alliances ORDER BY name")
    return [r["name"] for r in rows]

async def set_main_sb(alliance: str, member: str, sb: int):
    await bot.pool.execute(
        "UPDATE members SET main_sb=$1 WHERE alliance=$2 AND member=$3",
        sb, alliance, member
    )

async def set_active_alliance(guild_id: str, alliance: str):
    await bot.pool.execute(
        "INSERT INTO settings(guild_id, alliance) VALUES($1,$2) "
        "ON CONFLICT (guild_id) DO UPDATE SET alliance = EXCLUDED.alliance",
        guild_id, alliance
    )

async def get_active_alliance(guild_id: str) -> str | None:
    return await bot.pool.fetchval(
        "SELECT alliance FROM settings WHERE guild_id=$1", guild_id
    )

# -- Autocomplete -------------------------------------------------------------
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
            for r in rows if low in r["member"].lower()
        ][:25]
    return _ac

# -- War State Helpers --------------------------------------------------------
async def get_current_war(guild_id: str):
    return await bot.pool.fetchrow(
        "SELECT enemy_alliance, start_time FROM wars WHERE guild_id=$1", guild_id
    )

# -- UI View for war attacks -------------------------------------------------
class WarView(ui.View):
    def __init__(self, guild_id: str, cooldown_hours: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.cd = cooldown_hours

    async def populate(self):
        war = await get_current_war(self.guild_id)
        if not war:
            return
        rows = await bot.pool.fetch(
            "SELECT member, main_sb FROM members "
            "WHERE alliance=$1 ORDER BY main_sb DESC", war["enemy_alliance"]
        )
        now = datetime.datetime.utcnow()
        for rec in rows:
            last = await bot.pool.fetchval(
                "SELECT last_attack FROM war_attacks "
                "WHERE guild_id=$1 AND member=$2", 
                self.guild_id, rec["member"]
            )
            if last:
                elapsed = (now - last).total_seconds()
                rem = max(0, self.cd*3600 - elapsed)
            else:
                rem = 0

            if rem > 0:
                hrs = math.ceil(rem/3600)
                label, style, disabled = f"{hrs}h", ButtonStyle.danger, True
            else:
                label, style, disabled = "Attacked", ButtonStyle.primary, False

            btn = ui.Button(
                label=label,
                style=style,
                custom_id=f"war_atk:{rec['member']}",
                disabled=disabled
            )
            async def callback(inter: discord.Interaction, button=btn, member=rec['member']):
                # record attack
                await bot.pool.execute(
                    "INSERT INTO war_attacks(guild_id, member, last_attack) "
                    "VALUES($1,$2,NOW()) "
                    "ON CONFLICT (guild_id, member) DO UPDATE SET last_attack=NOW()",
                    self.guild_id, member
                )
                button.label = f"{self.cd}h"
                button.style = ButtonStyle.danger
                button.disabled = True
                await inter.response.edit_message(view=self)

            btn.callback = callback
            self.add_item(btn)

# -- War Embed Display -------------------------------------------------------
async def _show_war(inter: discord.Interaction):
    guild_id = str(inter.guild_id)
    war = await get_current_war(guild_id)
    own = await get_active_alliance(guild_id)
    # fetch counts
    async with bot.pool.acquire() as conn:
        A = await conn.fetchval("SELECT COUNT(*) FROM members WHERE alliance=$1", own)
        E = await conn.fetchval("SELECT COUNT(*) FROM members WHERE alliance=$1", war['enemy_alliance'])
        # WP map
        wp_map = {1:100,2:200,3:300,4:400,5:600,6:1000,7:1500,8:2000,9:2500}
        # warpoints for target
        main_e = await conn.fetch("SELECT main_sb FROM members WHERE alliance=$1", war['enemy_alliance'])
        col_e  = await conn.fetch("SELECT starbase FROM colonies WHERE alliance=$1", war['enemy_alliance'])
        main_o = await conn.fetch("SELECT main_sb FROM members WHERE alliance=$1", own)
        col_o  = await conn.fetch("SELECT starbase FROM colonies WHERE alliance=$1", own)

    own_wp = sum(wp_map.get(r['main_sb'],0) for r in main_e) + sum(wp_map.get(r['starbase'],0) for r in col_e)
    enemy_wp = sum(wp_map.get(r['main_sb'],0) for r in main_o) + sum(wp_map.get(r['starbase'],0) for r in col_o)

    # cooldowns swapped
    ratio_enemy = max(E/A,1)
    ratio_you   = max(A/E,1)
    T_enemy = round(4*ratio_enemy)
    T_you   = round(4*ratio_you)

    embed = discord.Embed(title=f"War! **{own}** vs **{war['enemy_alliance']}**",
                          color=discord.Color.red())
    embed.add_field(name="⚔️ Attacking cooldown", value=f"{T_enemy} hours", inline=True)
    embed.add_field(name="🛡️ Defending cooldown", value=f"{T_you} hours", inline=True)
    embed.add_field(name="​", value="​", inline=False)
    embed.add_field(name="⭐ WP/Raid", value=f"{own_wp:,}", inline=True)
    embed.add_field(name="★ Enemy WP/Raid", value=f"{enemy_wp:,}", inline=True)

    view = WarView(guild_id, T_enemy)
    await view.populate()
    await inter.response.send_message(embed=embed, view=view)

# -- Slash Commands ----------------------------------------------------------

@bot.tree.command(description="Create a new alliance.")
async def addalliance(inter: discord.Interaction, name: str):
    if await alliance_exists(name):
        return await inter.response.send_message("❌ Already exists.", ephemeral=True)
    await bot.pool.execute("INSERT INTO alliances(name) VALUES($1)", name)
    await inter.response.send_message(f"✅ Alliance **{name}** created.", ephemeral=True)

# ... keep your other commands unchanged (addmember, addcolony, etc.) ...

@bot.tree.command(description="Start a war against an enemy alliance.")
@app_commands.autocomplete(target=alliance_ac)
async def attack(inter: discord.Interaction, target: str):
    guild_id = str(inter.guild_id)
    if await get_current_war(guild_id):
        return await inter.response.send_message(
            "❌ A war is already in progress! Use `/war` to view it.", ephemeral=True)
    if not await alliance_exists(target):
        return await inter.response.send_message("❌ Enemy alliance not found.", ephemeral=True)
    await bot.pool.execute(
        "INSERT INTO wars(guild_id, enemy_alliance) VALUES($1,$2)",
        guild_id, target
    )
    await _show_war(inter)

@bot.tree.command(description="Show the current war and attack buttons.")
async def war(inter: discord.Interaction):
    if not await get_current_war(str(inter.guild_id)):
        return await inter.response.send_message(
            "❌ No war in progress. Start one with `/attack`.", ephemeral=True)
    await _show_war(inter)

@bot.tree.command(description="Password-protected: end the current war.")
async def endwar(inter: discord.Interaction, password: str):
    if password != ADMIN_PASS:
        return await inter.response.send_message("❌ Bad password.", ephemeral=True)
    if not await get_current_war(str(inter.guild_id)):
        return await inter.response.send_message("❌ No war to end.", ephemeral=True)
    await bot.pool.execute("DELETE FROM wars WHERE guild_id=$1", str(inter.guild_id))
    await inter.response.send_message("✅ War ended.", ephemeral=True)

# -- Run ----------------------------------------------------------------------
if __name__ == "__main__":
    TOKEN = os.getenv(token_env)
    if not TOKEN:
        raise RuntimeError(f"Set {token_env}")
    bot.run(TOKEN)
