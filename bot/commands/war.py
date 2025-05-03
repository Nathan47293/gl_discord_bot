# bot/commands/war.py

import discord
from discord import app_commands
from discord.ext import commands

# Import database helper functions
from ..db import (
    alliance_exists,        
    get_active_alliance,    
    all_alliances,
    ADMIN_PASS   # <-- new: used for /endwar
)
# Import the view that handles per-member buttons and timers
from bot.views import WarView

class WarCog(commands.Cog):
    """
    A Cog that handles the /attack command to start a war,
    calculates respawn cooldowns and warpoints, and displays
    an interactive embed (plus optional buttons via WarView).
    """
    def __init__(self, bot: commands.Bot):
        # Save bot reference to access its database pool
        self.bot = bot

    # ---------------------------------------------
    # Autocomplete callback for the 'target' parameter
    # ---------------------------------------------
    async def target_autocomplete(
        self,
        inter: discord.Interaction,
        current: str
    ) -> list[app_commands.Choice[str]]:
        """
        Called by Discord when the user is typing the 'target'
        argument for /attack. Filters all alliance names.
        """
        # Fetch all alliance names from the DB
        choices = await all_alliances(self.bot.pool)
        low = current.lower()
        # Return up to 25 matches containing the typed substring
        return [
            app_commands.Choice(name=a, value=a)
            for a in choices
            if low in a.lower()
        ][:25]

    async def get_war_embed_and_view(self, guild_id: str, own: str, target: str) -> tuple[discord.Embed, WarView]:
        async with self.bot.pool.acquire() as conn:
            A = await conn.fetchval("SELECT COUNT(*) FROM members WHERE alliance=$1", own)
            E = await conn.fetchval("SELECT COUNT(*) FROM members WHERE alliance=$1", target)
        ratio_enemy = max(E / A, 1)
        ratio_you   = max(A / E, 1)
        T_enemy = round(4 * ratio_enemy)
        T_you   = round(4 * ratio_you)
        wp_map = {1:100,2:200,3:300,4:400,5:600,6:1000,7:1500,8:2000,9:2500}
        async with self.bot.pool.acquire() as conn:
            main_enemy = await conn.fetch("SELECT main_sb FROM members WHERE alliance=$1", target)
            col_enemy  = await conn.fetch("SELECT starbase FROM colonies WHERE alliance=$1", target)
            main_own   = await conn.fetch("SELECT main_sb FROM members WHERE alliance=$1", own)
            col_own    = await conn.fetch("SELECT starbase FROM colonies WHERE alliance=$1", own)
        own_wp   = sum(wp_map.get(r["main_sb"], 0) for r in main_own) + sum(wp_map.get(r["starbase"], 0) for r in col_own)
        enemy_wp = sum(wp_map.get(r["main_sb"], 0) for r in main_enemy) + sum(wp_map.get(r["starbase"], 0) for r in col_enemy)
        embed = discord.Embed(
            title=f"War! **{own}** vs **{target}**",
            color=discord.Color.red()
        )
        embed.add_field(name="⚔️ Attacking cooldown", value=f"{T_enemy} hours", inline=True)
        embed.add_field(name="🛡️ Defending cooldown", value=f"{T_you} hours", inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        embed.add_field(name="⭐ WP/Raid", value=f"{enemy_wp:,}", inline=True)
        embed.add_field(name="★ Enemy WP/Raid", value=f"{own_wp:,}", inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)

        view = WarView(
            guild_id=guild_id,
            cooldown_hours=T_enemy,
            pool=self.bot.pool
        )
        await view.populate()
        return embed, view

    @app_commands.command(
        name="attack",
        description="Attack an enemy alliance: show respawn timers."
    )
    @app_commands.autocomplete(target=WarCog.target_autocomplete)
    async def attack(
        self,
        inter: discord.Interaction,
        target: str
    ):
        """
        /attack <target>
        (Only works if no war is presently active.)
        """
        # 1) Get your own alliance; must have been set first
        own = await get_active_alliance(self.bot.pool, str(inter.guild_id))
        if not own:
            return await inter.response.send_message(
                "❌ Please set your alliance first with /setalliance.",
                ephemeral=True
            )
        # NEW: If a war is active, /attack is disabled.
        if await get_current_war(self.bot.pool, str(inter.guild_id)):
            return await inter.response.send_message(
                "❌ War already in progress. Use /war to view the attack screen.",
                ephemeral=True
            )
        # Defer the response to allow extra processing time.
        await inter.response.defer()

        embed, view = await self.get_war_embed_and_view(str(inter.guild_id), own, target)
        await inter.followup.send(embed=embed, view=view)

    @app_commands.command(
        name="war",
        description="Display the current war attack screen."
    )
    async def war(
        self,
        inter: discord.Interaction
    ):
        """
        /war
        Displays the current war attack screen.
        Requires that:
         - Your active alliance is set.
         - A war record exists for this guild.
        """
        # Ensure your alliance is set.
        own = await get_active_alliance(self.bot.pool, str(inter.guild_id))
        if not own:
            return await inter.response.send_message(
                "❌ Set your alliance first with /setalliance.", ephemeral=True
            )
        # Retrieve the current war record.
        war_record = await get_current_war(self.bot.pool, str(inter.guild_id))
        if not war_record:
            return await inter.response.send_message(
                "❌ No active war.", ephemeral=True
            )
        # Use the enemy alliance from the war record.
        target = war_record["enemy_alliance"]

        embed, view = await self.get_war_embed_and_view(str(inter.guild_id), own, target)
        await inter.response.send_message(embed=embed, view=view)

    @app_commands.command(
        name="endwar",
        description="End the current war (password-protected)."
    )
    async def endwar(
        self,
        inter: discord.Interaction,
        password: str
    ):
        """
        /endwar <password>
        Ends the current war by deleting the war record. Once ended,
        you may start a new war using /attack with a new target alliance.
        """
        if password != ADMIN_PASS:
            return await inter.response.send_message(
                "❌ Bad password.", ephemeral=True
            )
        await self.bot.pool.execute(
            "DELETE FROM wars WHERE guild_id=$1",
            str(inter.guild_id)
        )
        await inter.response.send_message(
            "✅ War ended.", ephemeral=True
        )

# Setup function to register this Cog with the bot
async def setup(bot: commands.Bot):
    await bot.add_cog(WarCog(bot))