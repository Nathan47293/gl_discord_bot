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

# ────────────────────────────────────────────────────────────────────────────
# INTERNAL: build & send the war embed + interactive buttons
# ────────────────────────────────────────────────────────────────────────────
async def _show_war(inter: discord.Interaction):
    guild_id = str(inter.guild_id)
    pool = inter.client.pool

    # 1) Fetch war state
    war = await get_current_war(pool, guild_id)
    if not war:
        # no war exists
        return await inter.response.send_message(
            "❌ No war in progress.", ephemeral=True
        )
    enemy = war["enemy_alliance"]
    own   = await get_active_alliance(pool, guild_id)

    # 2) Compute cooldowns
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

    # 3) Compute warpoints
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
    wp_map = {1:100,2:200,3:300,4:400,5:600,6:1000,7:1500,8:2000,9:2500}
    own_wp   = sum(wp_map.get(r["main_sb"],0) for r in main_enemy) \
             + sum(wp_map.get(r["starbase"],0) for r in col_enemy)
    enemy_wp = sum(wp_map.get(r["main_sb"],0) for r in main_own)   \
             + sum(wp_map.get(r["starbase"],0) for r in col_own)

    # 4) Build embed layout
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
    # force next row
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

    # 5) Create and populate the button view
    view = WarView(guild_id, T_enemy, pool)
    await view.populate()

    # 6) Send initial or follow‑up
    if not inter.response.is_done():
        await inter.response.send_message(embed=embed, view=view)
    else:
        await inter.followup.send(embed=embed, view=view)


# ────────────────────────────────────────────────────────────────────────────
# Slash commands: /attack, /war, /endwar
# ────────────────────────────────────────────────────────────────────────────
class WarCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Autocomplete helper for the <target> param of /attack
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
        """Starts a new war, then shows the battle page."""
        gid = str(inter.guild_id)

        # prevent multiple wars
        if await get_current_war(self.bot.pool, gid):
            return await inter.response.send_message(
                "❌ A war is already in progress! Use `/war` to view it.",
                ephemeral=True
            )

        # verify enemy alliance
        if not await alliance_exists(self.bot.pool, target):
            return await inter.response.send_message(
                "❌ Enemy alliance not found.", ephemeral=True
            )

        # record war
        await self.bot.pool.execute(
            "INSERT INTO wars(guild_id, enemy_alliance) VALUES($1,$2)",
            gid, target
        )
        # display the war screen
        await _show_war(inter)

    @app_commands.command(
        name="war",
        description="Show the current war and attack timers."
    )
    async def war(self, inter: discord.Interaction):
        """Re-displays the active war page."""
        gid = str(inter.guild_id)
        if not await get_current_war(self.bot.pool, gid):
            return await inter.response.send_message(
                "❌ No war in progress. Start one with `/attack`.", ephemeral=True
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
        """Ends the active war (admin-only)."""
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


# required by discord.py to load this cog
async def setup(bot):
    await bot.add_cog(WarCog(bot))
