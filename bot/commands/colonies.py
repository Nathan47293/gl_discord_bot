# bot/commands/colonies.py

import discord
from discord import app_commands
from discord.ext import commands

# Import database helper functions and constants
from ..db import (
    member_exists,           # Checks whether a given member exists in an alliance
    colony_count,            # Returns how many colonies a member has
    get_members_with_colonies,# Retrieves all members plus their colonies for an alliance
    all_alliances,           # Fetches the list of all alliance names
    MAX_COLONIES,            # Maximum colonies allowed per member (e.g. 11)
    MAX_MEMBERS              # Maximum members allowed per alliance (e.g. 50)
)

class ColonyCog(commands.Cog):
    """
    A Cog that handles slash commands for managing colonies.
    """
    def __init__(self, bot: commands.Bot):
        # Keep a reference to the bot so we can access its database pool
        self.bot = bot

    async def alliance_autocomplete(
        self, inter: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """
        Autocomplete callback for the `alliance` parameter.
        Filters all alliance names from the database by the current input.
        """
        # Fetch all alliance names from the database
        choices = await all_alliances(self.bot.pool)
        low = current.lower()
        # Return up to 25 matches where the lowercase input is in the alliance name
        return [
            app_commands.Choice(name=a, value=a)
            for a in choices
            if low in a.lower()
        ][:25]

    async def member_autocomplete(
        self, inter: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """
        Autocomplete callback for the `member` parameter.
        Requires that `alliance` has been provided first in the command.
        """
        # Read the current alliance typed by the user
        alliance = inter.namespace.alliance
        if not alliance:
            # If no alliance yet, return no suggestions
            return []
        # Query the DB for members in that alliance
        rows = await self.bot.pool.fetch(
            "SELECT member FROM members WHERE alliance=$1 ORDER BY member",
            alliance
        )
        low = current.lower()
        # Filter and return up to 25 matching member names
        return [
            app_commands.Choice(name=r["member"], value=r["member"])
            for r in rows
            if low in r["member"].lower()
        ][:25]

    @app_commands.command(
        name="addcolony",
        description="Add a colony coordinate (max 11 per member)."
    )
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    @app_commands.autocomplete(member=member_autocomplete)
    async def addcolony(
        self,
        inter: discord.Interaction,
        alliance: str,
        member: str,
        starbase: app_commands.Range[int, 1, 9],
        x: int,
        y: int
    ):
        """
        /addcolony <alliance> <member> <starbase> <x> <y>
        Adds a new colony for the given member.
        - Validates that the member exists.
        - Enforces the maximum colonies per member.
        - Inserts the (alliance, member, starbase, x, y) tuple into the DB.
        """
        # 1) Ensure the member exists in that alliance
        if not await member_exists(self.bot.pool, alliance, member):
            return await inter.response.send_message(
                "❌ Member not found.", ephemeral=True
            )

        # 2) Check colony count limit
        count = await colony_count(self.bot.pool, alliance, member)
        if count >= MAX_COLONIES:
            return await inter.response.send_message(
                "❌ Max colonies reached.", ephemeral=True
            )

        # 3) Insert the new colony record
        await self.bot.pool.execute(
            "INSERT INTO colonies(alliance,member,starbase,x,y) "
            "VALUES($1,$2,$3,$4,$5)",
            alliance, member, starbase, x, y
        )

        # 4) Confirm success
        await inter.response.send_message(
            f"✅ Colony SB{starbase} ({x},{y}) added.", ephemeral=True
        )

    @app_commands.command(
        name="removecolony",
        description="Remove a specific colony."
    )
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    @app_commands.autocomplete(member=member_autocomplete)
    async def removecolony(
        self,
        inter: discord.Interaction,
        alliance: str,
        member: str,
        starbase: app_commands.Range[int, 1, 9],
        x: int,
        y: int
    ):
        """
        /removecolony <alliance> <member> <starbase> <x> <y>
        Deletes the specific colony matching all parameters.
        - Validates member exists.
        - Attempts DELETE and checks affected rows.
        """
        # 1) Ensure the member exists
        if not await member_exists(self.bot.pool, alliance, member):
            return await inter.response.send_message(
                "❌ Member not found.", ephemeral=True
            )

        # 2) Attempt deletion
        res = await self.bot.pool.execute(
            "DELETE FROM colonies "
            "WHERE alliance=$1 AND member=$2 AND starbase=$3 "
            "AND x=$4 AND y=$5",
            alliance, member, starbase, x, y
        )

        # The result string ends with the number of rows deleted, e.g. "DELETE 1"
        if res.endswith("0"):
            return await inter.response.send_message(
                "❌ No such colony.", ephemeral=True
            )

        # 3) Confirm removal
        await inter.response.send_message(
            f"✅ Removed SB{starbase} ({x},{y}).", ephemeral=True
        )

    @app_commands.command(
        name="show",
        description="Show an alliance’s members & colonies."
    )
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    async def show(
        self,
        inter: discord.Interaction,
        alliance: str
    ):
        """
        /show <alliance>
        Retrieves all members and their colonies for the given alliance,
        then builds and sends a richly formatted embed:
        - Title shows current/maximum members.
        - Each member appears with their main SB, colony count, and coordinates.
        - Footer shows total colonies discovered.
        """
        # 1) Fetch members with their colony lists and main SB
        data = await get_members_with_colonies(self.bot.pool, alliance)

        # 2) Build embed skeleton
        title = f"{alliance} ({len(data)}/{MAX_MEMBERS} members)"
        embed = discord.Embed(title=title, color=discord.Color.blue())

        total_cols = 0

        # 3) Add one field per member
        for member_name, cnt, cols, main_sb in data:
            total_cols += cnt
            header = f"{member_name} (SB{main_sb} — {cnt}/{MAX_COLONIES})"

            if not cols:
                # No colonies recorded
                embed.add_field(name=header, value="—", inline=False)
            else:
                # Format each colony as `SB<level> (x,y)` on its own line
                lines = "\n".join(f"SB{sb} ({xx},{yy})" for sb, xx, yy in cols)
                embed.add_field(name=header, value=lines, inline=False)

        # 4) Add footer summarizing total colonies
        embed.set_footer(text=f"{total_cols} colonies discovered")

        # 5) Send the embed to the channel
        await inter.response.send_message(embed=embed)

# Register this Cog with the bot
async def setup(bot: commands.Bot):
    await bot.add_cog(ColonyCog(bot))