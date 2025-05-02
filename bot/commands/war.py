# bot/commands/war.py
import datetime
import math
import discord
from discord import app_commands
from discord.ext import commands

from ..db import (
    alliance_exists,
    get_active_alliance,
    get_current_war,
    all_alliances,
)
from ..views import WarView

# —————————————————————————————————————————————
# Helper to render the war embed + buttons
# —————————————————————————————————————————————
async def _show_war(inter: discord.Interaction):
    guild_id = str(inter.guild_id)
    pool = inter.client.pool

    # Fetch war state
    war = await get_current_war(pool, guild_id)
    enemy = war["enemy_alliance"]
    own   = await get_active_alliance(pool, guild_id)

    # Calculate member counts for cooldowns
    async with pool.acquire() as conn:
        A = await conn.fetchval(
            "SELECT COUNT(*) FROM members WHERE alliance=$1", own
        )
        E = await conn.fetchval(
            "SELECT COUNT(*) FROM members WHERE alliance=$1", enemy
        )

    # Compute swapped cooldowns
    ratio_enemy = max(E / A, 1)
    ratio_you   = max(A / E, 1)
    T_enemy = round(4 * ratio_enemy)
    T_you   = round(4 * ratio_you)

    # Fetch SBs for WP calculation
    async with pool.acquire() as conn:
        main_enemy = await conn.fetch(
            "SELECT main_sb FROM members WHERE alliance=$1", enemy
        )
        col_enemy  = await conn.fetch(
            "SELECT starbase FROM colonies WHERE alliance=$1", enemy
        )
        main_own   = await conn.fetch(
            "SELECT main_sb FROM members WHERE alliance=$1", own
        )
        col_own    = await conn.fetch(
            "SELECT starbase FROM colonies WHERE alliance=$1", own
        )

    # Map SB → WP
    wp_map = {1:100,2:200,3:300,4:400,5:600,6:1000,7:1500,8:2000,9:2500}

    own_wp   = sum(wp_map.get(r["main_sb"],0)    for r in main_enemy) + \
               sum(wp_map.get(r["starbase"],0)   for r in col_enemy)
    enemy_wp = sum(wp_map.get(r["main_sb"],0)    for r in main_own)   + \
               sum(wp_map.get(r["starbase"],0)   for r in col_own)

    # Build embed: two inline fields per row
    embed = discord.Embed(
        title=f"War! **{own}** vs **{enemy}**",
        color=discord.Color.red()
    )
    embed.add_field(
        name="⚔️ Attacking cooldown", 
        value=f"{T_enemy} hours", 
        inline=True
    )
    embed.add_field(
        name="🛡️ Defending cooldown", 
        value=f"{T_you} hours", 
        inline=True
    )
    # blank field to break row
    embed.add_field(name="\u200b", value="\u200b", inline=False)
    embed.add_field(
        name="⭐ WP/Raid", 
        value=f"{own_wp:,}", 
        inline=True
    )
    embed.add_field(
        name="★ Enemy WP/Raid", 
        value=f"{enemy_wp:,}", 
        inline=True
    )

    # Build interactive buttons view
    view = WarView(guild_id, T_enemy, pool)
    await view.populate()
    await inter.response.send_message(embed=embed, view=view)

# —————————————————————————————————————————————
# Cog with /attack, /war, /endwar
# —————————————————————————————————————————————
class WarCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Autocomplete for target alliance
    async def target_autocomplete(
        self, inter: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        opts = await all_alliances(self.bot.pool)
        low = current.lower()
        return [
            app_commands.Choice(name=o, value=o)
            for o in opts if low in o.lower()
        ][:25]

    @app_commands.command(
        name="attack",
        description="Start a war against an enemy alliance."
    )
    @app_commands.autocomplete(target=target_autocomplete)
    async def attack(
        self,
        inter: discord.Interaction,
        target: str
    ):
        gid = str(inter.guild_id)
        # ensure no war in progress
        if await get_current_war(self.bot.pool, gid):
            return await inter.response.send_message(
                "❌ A war is already in progress! Use `/war` to view it.", 
                ephemeral=True
            )
        # validate target
        if not await alliance_exists(self.bot.pool, target):
            return await inter.response.send_message(
                "❌ Enemy alliance not found.", ephemeral=True
            )
        # start war
        await self.bot.pool.execute(
            "INSERT INTO wars(guild_id, enemy_alliance) VALUES($1,$2)",
            gid, target
        )
        # show the live war page
        await _show_war(inter)

    @app_commands.command(
        name="war",
        description="Show the current war and attack buttons."
    )
    async def war(self, inter: discord.Interaction):
        gid = str(inter.guild_id)
        if not await get_current_war(self.bot.pool, gid):
            return await inter.response.send_message(
                "❌ No war in progress. Start one with `/attack`.", 
                ephemeral=True
            )
        await _show_war(inter)

    @app_commands.command(
        name="endwar",
        description="Password-protected: end the current war."
    )
    async def endwar(
        self,
        inter: discord.Interaction,
        password: str
    ):
        if password != self.bot.ADMIN_PASS:
            return await inter.response.send_message("❌ Bad password.", ephemeral=True)
        gid = str(inter.guild_id)
        if not await get_current_war(self.bot.pool, gid):
            return await inter.response.send_message(
                "❌ No war to end.", ephemeral=True
            )
        await self.bot.pool.execute(
            "DELETE FROM wars WHERE guild_id=$1", gid
        )
        await inter.response.send_message("✅ War ended.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(WarCog(bot))
