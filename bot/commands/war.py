# bot/commands/war.py
import discord
from discord import app_commands
from discord.ext import commands

from ..db import alliance_exists, get_active_alliance
from ..db import all_alliances  # for autocomplete
from ..views import WarView  # assumes your view only handles button layout

class WarCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Autocomplete for the target alliance
    async def target_autocomplete(
        self, inter: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        choices = await all_alliances(self.bot.pool)
        low = current.lower()
        return [
            app_commands.Choice(name=a, value=a)
            for a in choices
            if low in a.lower()
        ][:25]

    @app_commands.command(
        name="attack",
        description="Attack an enemy alliance: show respawn timers."
    )
    @app_commands.autocomplete(target=target_autocomplete)
    async def attack(
        self,
        inter: discord.Interaction,
        target: str
    ):
        # exactly the logic you already had in galaxy_life_bot.py
        own = await get_active_alliance(self.bot.pool, str(inter.guild_id))
        if not own:
            return await inter.response.send_message(
                "❌ Please set your alliance first with /setalliance.", ephemeral=True
            )

        if not await alliance_exists(self.bot.pool, target):
            return await inter.response.send_message(
                "❌ Enemy alliance not found.", ephemeral=True
            )

        async with self.bot.pool.acquire() as conn:
            A = await conn.fetchval(
                "SELECT COUNT(*) FROM members WHERE alliance=$1", own
            )
            E = await conn.fetchval(
                "SELECT COUNT(*) FROM members WHERE alliance=$1", target
            )

        # swapped cooldown sides
        ratio_enemy = max(E / A, 1)
        ratio_you   = max(A / E, 1)
        T_enemy = round(4 * ratio_enemy)
        T_you   = round(4 * ratio_you)

        # warpoints calculation
        wp_map = {1: 100,2:200,3:300,4:400,5:600,6:1000,7:1500,8:2000,9:2500}

        async with self.bot.pool.acquire() as conn:
            main_enemy = await conn.fetch(
                "SELECT main_sb FROM members WHERE alliance=$1", target
            )
            col_enemy = await conn.fetch(
                "SELECT starbase FROM colonies WHERE alliance=$1", target
            )
            main_own = await conn.fetch(
                "SELECT main_sb FROM members WHERE alliance=$1", own
            )
            col_own = await conn.fetch(
                "SELECT starbase FROM colonies WHERE alliance=$1", own
            )

        own_wp = sum(wp_map.get(r["main_sb"], 0) for r in main_enemy) \
               + sum(wp_map.get(r["starbase"], 0) for r in col_enemy)
        enemy_wp = sum(wp_map.get(r["main_sb"], 0) for r in main_own) \
                 + sum(wp_map.get(r["starbase"], 0) for r in col_own)

        embed = discord.Embed(
            title=f"War! **{own}** vs **{target}**",
            color=discord.Color.red()
        )
        embed.add_field(name="⚔️ Attacking cooldown", value=f"{T_enemy} hours", inline=True)
        embed.add_field(name="🛡️ Defending cooldown", value=f"{T_you} hours", inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=False)
        embed.add_field(name="⭐ WP/Raid", value=f"{own_wp:,}", inline=True)
        embed.add_field(name="★ Enemy WP/Raid", value=f"{enemy_wp:,}", inline=True)

        # if you still have the WarView for buttons, you can optionally include it here.
        # Otherwise just send the embed:
        await inter.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(WarCog(bot))
