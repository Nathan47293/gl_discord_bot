# bot/commands/members.py

import discord
from discord import app_commands
from discord.ext import commands

# Import database helper functions
from ..db import (
    alliance_exists,  # Check if an alliance exists in the DB
    member_exists,    # Check if a member exists within a given alliance
    set_main_sb,      # Update a member's main starbase level
    all_alliances,    # Retrieve a list of all alliance names
    ADMIN_PASS        # Admin password for protected commands
)

class MemberCog(commands.Cog):
    """
    A Cog for slash commands related to alliance members: adding,
    renaming, and setting their main starbase level.
    """

    def __init__(self, bot: commands.Bot):
        # Store a reference to the bot to access its DB pool
        self.bot = bot

    # ---------------------------------------------
    # Autocomplete callback for alliance names
    # ---------------------------------------------
    async def alliance_autocomplete(
        self, inter: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """
        Called by Discord when the user is typing the 'alliance'
        parameter in a slash command. Filters all alliance names
        by the current typed substring.
        """
        # Fetch all alliance names from the DB
        choices = await all_alliances(self.bot.pool)
        low = current.lower()
        # Return up to 25 suggestions matching 'current'
        return [
            app_commands.Choice(name=a, value=a)
            for a in choices
            if low in a.lower()
        ][:25]

    # ---------------------------------------------
    # Autocomplete callback for member names
    # ---------------------------------------------
    async def member_autocomplete(
        self, inter: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """
        Called when typing the 'member' parameter. Depends on
        the 'alliance' parameter already being filled.
        """
        # Retrieve the currently entered alliance from the interaction
        alliance = inter.namespace.alliance
        if not alliance:
            # No autocomplete suggestions until alliance is set
            return []
        # Query DB for all members in that alliance
        rows = await self.bot.pool.fetch(
            "SELECT member FROM members WHERE alliance=$1 ORDER BY member",
            alliance
        )
        low = current.lower()
        # Filter and return up to 25 matching members
        return [
            app_commands.Choice(name=r["member"], value=r["member"])
            for r in rows
            if low in r["member"].lower()
        ][:25]

    @app_commands.command(
        name="addmember",
        description="Add a member to an alliance (with main SB)."
    )
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    @app_commands.autocomplete(member=member_autocomplete)
    async def addmember(
        self,
        inter: discord.Interaction,
        alliance: str,
        member: str,
        main_sb: app_commands.Range[int, 1, 9]
    ):
        """
        /addmember <alliance> <member> <main_sb>
        Adds a new member with a specified main starbase level.
        1) Validates the alliance exists.
        2) Ensures the member isn't already registered.
        3) Inserts into the DB.
        """
        # 1) Alliance must exist
        if not await alliance_exists(self.bot.pool, alliance):
            return await inter.response.send_message(
                "❌ Alliance not found.", ephemeral=True
            )
        # 2) Member must not already exist
        if await member_exists(self.bot.pool, alliance, member):
            return await inter.response.send_message(
                "❌ Member exists.", ephemeral=True
            )
        # 3) Insert the new member
        await self.bot.pool.execute(
            "INSERT INTO members(alliance,member,main_sb) VALUES($1,$2,$3)",
            alliance, member, main_sb
        )
        # 4) Confirm to user
        await inter.response.send_message(
            f"✅ Added **{member}** (SB{main_sb}).", ephemeral=True
        )

    @app_commands.command(
        name="setmainsb",
        description="Update a member’s main starbase level."
    )
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    @app_commands.autocomplete(member=member_autocomplete)
    async def setmainsb(
        self,
        inter: discord.Interaction,
        alliance: str,
        member: str,
        sb: app_commands.Range[int, 1, 9],
        password: str
    ):
        """
        /setmainsb <alliance> <member> <sb>
        Updates an existing member's main starbase level.
        1) Validates the member exists.
        2) Updates main_sb in DB.
        """
        # Verify admin password
        if password != ADMIN_PASS:
            return await inter.response.send_message("❌ Bad password.", ephemeral=True)
        # 1) Member must exist
        if not await member_exists(self.bot.pool, alliance, member):
            return await inter.response.send_message("❌ Member not found.", ephemeral=True)
        # 2) Update the DB
        await set_main_sb(self.bot.pool, alliance, member, sb)
        # 3) Confirm to user
        await inter.response.send_message(f"✅ **{member}**’s main SB set to {sb}.", ephemeral=True)

    @app_commands.command(
        name="removemember",
        description="Remove a member (and all their colonies)."
    )
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    @app_commands.autocomplete(member=member_autocomplete)
    async def removemember(
        self,
        inter: discord.Interaction,
        alliance: str,
        member: str,
        password: str
    ):
        """
        /removemember <alliance> <member>
        Deletes a member and cascades to remove their colonies.
        1) Validates member exists.
        2) Deletes from DB.
        """
        # Verify admin password
        if password != ADMIN_PASS:
            return await inter.response.send_message("❌ Bad password.", ephemeral=True)
        # 1) Member must exist
        if not await member_exists(self.bot.pool, alliance, member):
            return await inter.response.send_message("❌ Member not found.", ephemeral=True)
        # 2) Delete from DB
        await self.bot.pool.execute(
            "DELETE FROM members WHERE alliance=$1 AND member=$2",
            alliance, member
        )
        # 3) Confirm to user
        await inter.response.send_message(f"✅ Removed **{member}**.", ephemeral=True)

    @app_commands.command(
        name="renamemember",
        description="Rename a member (keeps colonies)."
    )
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    @app_commands.autocomplete(old=member_autocomplete)  # Autocomplete for the `old` parameter
    async def renamemember(
        self,
        inter: discord.Interaction,
        alliance: str,
        old: str,   # original name
        new: str    # new desired name
    ):
        """
        /renamemember <alliance> <old> <new>
        Renames a member:
        1) Validates `old` exists and `new` is not taken.
        2) Inserts `new`, updates colonies, deletes `old`—all in one transaction.
        """
        # 1) Validate existence and uniqueness
        if not await member_exists(self.bot.pool, alliance, old):
            return await inter.response.send_message(
                "❌ Original not found.", ephemeral=True
            )
        if await member_exists(self.bot.pool, alliance, new):
            return await inter.response.send_message(
                "❌ New name taken.", ephemeral=True
            )
        # 2) Perform rename transactionally
        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                # Fetch main_sb for transfer
                main_sb = await conn.fetchval(
                    "SELECT main_sb FROM members WHERE alliance=$1 AND member=$2",
                    alliance, old
                ) or 0
                # Insert the new member record
                await conn.execute(
                    "INSERT INTO members(alliance,member,main_sb) VALUES($1,$2,$3)",
                    alliance, new, main_sb
                )
                # Reassign all colonies
                await conn.execute(
                    "UPDATE colonies SET member=$1 WHERE alliance=$2 AND member=$3",
                    new, alliance, old
                )
                # Delete the old member record
                await conn.execute(
                    "DELETE FROM members WHERE alliance=$1 AND member=$2",
                    alliance, old
                )
        # 3) Confirm to user
        await inter.response.send_message(
            f"✅ Renamed **{old}** to **{new}**.", ephemeral=True
        )

# Register this Cog so discord.py picks up these commands
async def setup(bot: commands.Bot):
    await bot.add_cog(MemberCog(bot))