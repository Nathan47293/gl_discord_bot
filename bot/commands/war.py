# bot/commands/war.py

import discord
from discord import app_commands
from discord.ext import commands

# Import database helper functions
from ..db import (
    alliance_exists,        # Checks if a named alliance exists
    get_active_alliance,    # Retrieves the active alliance for this guild
    all_alliances           # Returns list of all alliances (for autocomplete)
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
        """
        /attack <target>
        1) Validates that you have set an active alliance (/setalliance).
        2) Validates that the target alliance exists.
        3) Calculates:
           - A = number of members in your alliance
           - E = number of members in enemy alliance
        4) Computes cooldowns (swapped sides) as 4 * ratio.
        5) Fetches main SB and colony SBs for both sides.
        6) Converts SB levels to warpoints and totals them per side.
        7) Builds an embed with cooldowns and WP/Raid stats.
        8) Optionally attaches a WarView for interactive buttons.
        """
        # 1) Get your own alliance, must have been set first
        own = await get_active_alliance(
            self.bot.pool, str(inter.guild_id)
        )
        if not own:
            # If not set, prompt user to call /setalliance
            return await inter.response.send_message(
                "❌ Please set your alliance first with /setalliance.",
                ephemeral=True
            )

        # 2) Ensure the enemy alliance exists
        if not await alliance_exists(self.bot.pool, target):
            return await inter.response.send_message(
                "❌ Enemy alliance not found.", ephemeral=True
            )
        
        # Defer the response to allow extra processing time.
        await inter.response.defer()

        # 3) Query alliance sizes A (yours) and E (enemy)
        async with self.bot.pool.acquire() as conn:
            A = await conn.fetchval(
                "SELECT COUNT(*) FROM members WHERE alliance=$1", own
            )
            E = await conn.fetchval(
                "SELECT COUNT(*) FROM members WHERE alliance=$1", target
            )

        # 4) Compute respawn cooldowns
        #    ratio_enemy = E/A, ratio_you = A/E, minimum 1
        ratio_enemy = max(E / A, 1)
        ratio_you   = max(A / E, 1)
        T_enemy = round(4 * ratio_enemy)
        T_you   = round(4 * ratio_you)

        # 5) Prepare warpoints conversion map for SB levels
        wp_map = {1:100,2:200,3:300,4:400,5:600,
                  6:1000,7:1500,8:2000,9:2500}

        # 6) Fetch each side's SB levels
        async with self.bot.pool.acquire() as conn:
            main_enemy = await conn.fetch(
                "SELECT main_sb FROM members WHERE alliance=$1", target
            )
            col_enemy  = await conn.fetch(
                "SELECT starbase FROM colonies WHERE alliance=$1", target
            )
            main_own   = await conn.fetch(
                "SELECT main_sb FROM members WHERE alliance=$1", own
            )
            col_own    = await conn.fetch(
                "SELECT starbase FROM colonies WHERE alliance=$1", own
            )

        # 7) Sum warpoints: for each record, map SB to wp and total
        own_wp   = sum(wp_map.get(r["main_sb"], 0) for r in main_own) + \
                   sum(wp_map.get(r["starbase"],0) for r in col_own)
        enemy_wp = sum(wp_map.get(r["main_sb"], 0) for r in main_enemy) + \
                   sum(wp_map.get(r["starbase"],0) for r in col_enemy)

        # 8) Build the embed
        embed = discord.Embed(
            title=f"War! **{own}** vs **{target}**",
            color=discord.Color.red()
        )
        # Add inline fields for the two cooldowns
        embed.add_field(
            name="⚔️ Attacking cooldown", value=f"{T_enemy} hours", inline=True
        )
        embed.add_field(
            name="🛡️ Defending cooldown", value=f"{T_you} hours", inline=True
        )
        # Add a zero-width field to move to next line
        embed.add_field(name="\u200b", value="\u200b", inline=False)
        # Add WP/Raid stats beneath their respective cooldowns
        embed.add_field(
            name="⭐ WP/Raid", value=f"{own_wp:,}", inline=True
        )
        embed.add_field(
            name="★ Enemy WP/Raid", value=f"{enemy_wp:,}", inline=True
        )

        # 9) Send the embed; optionally mount your WarView here
        view = WarView(
            guild_id=str(inter.guild_id),  # Pass the guild ID
            cooldown_hours=4,             # Use the cooldown duration (e.g., 4 hours)
            pool=self.bot.pool            # Pass the database connection pool
        )
        await view.populate()  # Dynamically populate the buttons
        await inter.followup.send(embed=embed, view=view)
        

# Setup function to register this Cog with the bot
async def setup(bot: commands.Bot):
    await bot.add_cog(WarCog(bot))