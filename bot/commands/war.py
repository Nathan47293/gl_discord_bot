# bot/commands/war.py
import datetime, math
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

# Copy the _show_war helper into this file (or import it if you moved it)
async def _show_war(inter: discord.Interaction):
    guild_id = str(inter.guild_id)
    war = await get_current_war(inter.client.pool, guild_id)
    own = await get_active_alliance(inter.client.pool, guild_id)

    async with inter.client.pool.acquire() as conn:
        A = await conn.fetchval("SELECT COUNT(*) FROM members WHERE alliance=$1", own)
        E = await conn.fetchval("SELECT COUNT(*) FROM members WHERE alliance=$1", war["enemy_alliance"])
        wp_map = {1:100,2:200,3:300,4:400,5:600,6:1000,7:1500,8:2000,9:2500}
        main_enemy = await conn.fetch("SELECT main_sb FROM members WHERE alliance=$1", war["enemy_alliance"])
        col_enemy  = await conn.fetch("SELECT starbase FROM colonies WHERE alliance=$1", war["enemy_alliance"])
        main_own   = await conn.fetch("SELECT main_sb FROM members WHERE alliance=$1", own)
        col_own    = await conn.fetch("SELECT starbase FROM colonies WHERE alliance=$1", own)

    own_wp   = sum(wp_map[r["main_sb"]] for r in main_enemy) + sum(wp_map[r["starbase"]] for r in col_enemy)
    enemy_wp = sum(wp_map[r["main_sb"]] for r in main_own)   + sum(wp_map[r["starbase"]] for r in col_own)

    ratio_enemy = max(E / A, 1); T_enemy = round(4 * ratio_enemy)
    ratio_you   = max(A / E, 1); T_you   = round(4 * ratio_you)

    embed = discord.Embed(
        title=f"War! **{own}** vs **{war['enemy_alliance']}**",
        color=discord.Color.red()
    )
    embed.add_field("⚔️ Attacking cooldown", f"{T_enemy} hours", inline=True)
    embed.add_field("🛡️ Defending cooldown", f"{T_you} hours", inline=True)
    embed.add_field("\u200b", "\u200b", inline=False)
    embed.add_field("⭐ WP/Raid", f"{own_wp:,}", inline=True)
    embed.add_field("★ Enemy WP/Raid", f"{enemy_wp:,}", inline=True)

    view = WarView(guild_id, T_enemy, inter.client.pool)
    await view.populate()
    await inter.response.send_message(embed=embed, view=view)


class WarCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def target_autocomplete(self, inter: discord.Interaction, current: str):
        choices = await all_alliances(self.bot.pool)
        low = current.lower()
        return [
            app_commands.Choice(name=a, value=a)
            for a in choices
            if low in a.lower()
        ][:25]

    @app_commands.command(
        name="attack",
        description="Start a war against an enemy alliance."
    )
    @app_commands.autocomplete(target=target_autocomplete)
    async def attack(self, inter: discord.Interaction, target: str):
        gid = str(inter.guild_id)
        if await get_current_war(self.bot.pool, gid):
            return await inter.response.send_message("❌ War already in progress—use /war.", ephemeral=True)
        if not await alliance_exists(self.bot.pool, target):
            return await inter.response.send_message("❌ Enemy alliance not found.", ephemeral=True)
        await self.bot.pool.execute("INSERT INTO wars(guild_id, enemy_alliance) VALUES($1,$2)", gid, target)
        await _show_war(inter)

    @app_commands.command(
        name="war",
        description="Show the current war and attack buttons."
    )
    async def war(self, inter: discord.Interaction):
        if not await get_current_war(self.bot.pool, str(inter.guild_id)):
            return await inter.response.send_message("❌ No war in progress—start one with /attack.", ephemeral=True)
        await _show_war(inter)

    @app_commands.command(
        name="endwar",
        description="Password-protected: end the current war."
    )
    async def endwar(self, inter: discord.Interaction, password: str):
        if password != self.bot.ADMIN_PASS:
            return await inter.response.send_message("❌ Bad password.", ephemeral=True)
        if not await get_current_war(self.bot.pool, str(inter.guild_id)):
            return await inter.response.send_message("❌ No war to end.", ephemeral=True)
        await self.bot.pool.execute("DELETE FROM wars WHERE guild_id=$1", str(inter.guild_id))
        await inter.response.send_message("✅ War ended.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(WarCog(bot))
