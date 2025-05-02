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


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

async def _render_war(inter: discord.Interaction, pool: discord.ext.commands.Bot.pool):
    """
    Fetches the current war data, builds the embed + interactive WarView,
    and sends it exactly once via inter.response.send_message.
    """
    guild_id = str(inter.guild_id)

    # 1) Get war row
    war = await get_current_war(pool, guild_id)
    if not war:
        # No war exists
        return await inter.response.send_message(
            "❌ No war in progress.", ephemeral=True
        )

    enemy = war["enemy_alliance"]
    own   = await get_active_alliance(pool, guild_id)

    # 2) Calculate cooldowns based on alliance sizes
    async with pool.acquire() as conn:
        A = await conn.fetchval(
            "SELECT COUNT(*) FROM members WHERE alliance=$1", own
        )
        E = await conn.fetchval(
            "SELECT COUNT(*) FROM members WHERE alliance=$1", enemy
        )
    ratio_enemy = max(E / A, 1)
    ratio_you   = max(A / E, 1)
    T_enemy = round(4 * ratio_enemy)
    T_you   = round(4 * ratio_you)

    # 3) Calculate warpoints for each side
    async with pool.acquire() as conn:
        main_e = await conn.fetch(
            "SELECT main_sb FROM members WHERE alliance=$1", enemy
        )
        col_e  = await conn.fetch(
            "SELECT starbase FROM colonies WHERE alliance=$1", enemy
        )
        main_o = await conn.fetch(
            "SELECT main_sb FROM members WHERE alliance=$1", own
        )
        col_o  = await conn.fetch(
            "SELECT starbase FROM colonies WHERE alliance=$1", own
        )

    wp_map = {
        1: 100, 2: 200, 3: 300, 4: 400, 5: 600,
        6: 1000, 7: 1500, 8: 2000, 9: 2500
    }
    own_wp   = sum(wp_map.get(r["main_sb"], 0)    for r in main_e) + \
               sum(wp_map.get(r["starbase"], 0) for r in col_e)
    enemy_wp = sum(wp_map.get(r["main_sb"], 0)    for r in main_o) + \
               sum(wp_map.get(r["starbase"], 0) for r in col_o)

    # 4) Build the embed with two inline fields per row
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
    # force newline
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

    # 5) Create and populate the interactive button view
    view = WarView(guild_id, T_enemy, pool)
    await view.populate()

    # 6) Send exactly one response
    await inter.response.send_message(embed=embed, view=view)


# ─────────────────────────────────────────────────────────────────────────────
# COG: War commands (/attack, /war, /endwar)
# ─────────────────────────────────────────────────────────────────────────────

class WarCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def target_autocomplete(
        self, inter: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """
        Autocomplete callback for the `target` parameter of /attack.
        """
        opts = await all_alliances(self.bot.pool)
        low = current.lower()
        return [
            app_commands.Choice(name=a, value=a)
            for a in opts
            if low in a.lower()
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
        """
        /attack <target> — begins a new war if none is active,
        then renders the war page with timers, WP stats, and buttons.
        """
        guild_id = str(inter.guild_id)

        # Prevent duplicate wars
        if await get_current_war(self.bot.pool, guild_id):
            return await inter.response.send_message(
                "❌ A war is already in progress! Use `/war` to view it.",
                ephemeral=True
            )

        # Validate target alliance
        if not await alliance_exists(self.bot.pool, target):
            return await inter.response.send_message(
                "❌ Enemy alliance not found.", ephemeral=True
            )

        # Insert war row
        await self.bot.pool.execute(
            "INSERT INTO wars(guild_id, enemy_alliance) VALUES($1,$2)",
            guild_id, target
        )

        # Render
        await _render_war(inter, self.bot.pool)

    @app_commands.command(
        name="war",
        description="Show the current war and attack timers."
    )
    async def war(self, inter: discord.Interaction):
        """
        /war — re-displays the active war page.
        """
        guild_id = str(inter.guild_id)
        if not await get_current_war(self.bot.pool, guild_id):
            return await inter.response.send_message(
                "❌ No war in progress. Start one with `/attack`.",
                ephemeral=True
            )

        await _render_war(inter, self.bot.pool)

    @app_commands.command(
        name="endwar",
        description="Password-protected: end the current war."
    )
    async def endwar(
        self,
        inter: discord.Interaction,
        password: str
    ):
        """
        /endwar <password> — ends the current war (admin only).
        """
        guild_id = str(inter.guild_id)

        if password != self.bot.ADMIN_PASS:
            return await inter.response.send_message(
                "❌ Bad password.", ephemeral=True
            )

        if not await get_current_war(self.bot.pool, guild_id):
            return await inter.response.send_message(
                "❌ No war to end.", ephemeral=True
            )

        # Delete war row
        await self.bot.pool.execute(
            "DELETE FROM wars WHERE guild_id=$1", guild_id
        )

        await inter.response.send_message(
            "✅ War ended.", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(WarCog(bot))
