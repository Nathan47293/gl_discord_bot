# bot/commands/alliances.py

import discord
from discord import app_commands
from discord.ext import commands

# Import helper functions and constants from your database module
from ..db import (
    alliance_exists,     # checks if an alliance name already exists in the DB
    all_alliances,       # retrieves a list of all alliance names
    set_active_alliance, # marks one alliance as “active” for this guild
    ADMIN_PASS           # the admin password required for protected commands
)

class AllianceCog(commands.Cog):
    """
    A Cog (collection of related commands) for managing alliances.
    """

    def __init__(self, bot):
        # Store a reference to the bot instance so we can access its database pool
        self.bot = bot

    @app_commands.command(
        name="addalliance",
        description="Create a new alliance."
    )
    async def addalliance(
        self,
        inter: discord.Interaction,
        name: str
    ):
        """
        /addalliance <name>
        Creates a new alliance record in the database.
        - If the alliance already exists, we send an error.
        - Otherwise, we insert it and confirm success.
        """
        # 1) Check if the name is already taken
        if await alliance_exists(self.bot.pool, name):
            # ephemeral=True: only the command user sees this message
            return await inter.response.send_message(
                "❌ Already exists.", ephemeral=True
            )

        # 2) Insert the new alliance into the DB
        await self.bot.pool.execute(
            "INSERT INTO alliances(name) VALUES($1)",
            name
        )

        # 3) Confirm creation
        await inter.response.send_message(
            f"✅ Alliance **{name}** created.", ephemeral=True
        )

    @app_commands.command(
        name="list",
        description="List all alliances."
    )
    async def list_all(
        self,
        inter: discord.Interaction
    ):
        """
        /list
        Fetches all alliance names and prints them as a bullet list.
        If there are none, informs the user.
        """
        try:
            # 1) Retrieve the list of names
            opts = await all_alliances(self.bot.pool)

            # 2) If empty, let the user know
            if not opts:
                await inter.response.send_message(
                    "❌ No alliances recorded.", ephemeral=True
                )
                return

            # 3) Otherwise join them with newlines and send
            formatted = "\n".join(f"- {o}" for o in opts)
            await inter.response.send_message(formatted)
        except discord.InteractionResponded:
            # Already responded somehow, ignore
            pass
        except Exception as e:
            print(f"Error in list command: {e}")
            try:
                if not inter.response.is_done():
                    await inter.response.send_message("❌ An error occurred.", ephemeral=True)
            except:
                pass

    @app_commands.command(
        name="setalliance",
        description="Password-protected: set this guild’s alliance."
    )
    async def setalliance(
        self,
        inter: discord.Interaction,
        alliance: str,
        password: str
    ):
        """
        /setalliance <alliance> <password>
        Marks one alliance as the “active” one for this Discord guild.
        Only works if the correct admin password is provided.
        """
        # 1) Verify password
        if password != ADMIN_PASS:
            return await inter.response.send_message(
                "❌ Bad password.", ephemeral=True
            )

        # 2) Verify the alliance actually exists in the DB
        if not await alliance_exists(self.bot.pool, alliance):
            return await inter.response.send_message(
                "❌ Alliance not found.", ephemeral=True
            )

        # 3) Store the active alliance for this guild (by guild ID)
        await set_active_alliance(
            self.bot.pool,
            str(inter.guild_id),
            alliance
        )

        # 4) Confirm success
        await inter.response.send_message(
            f"✅ Active alliance set to **{alliance}**.", ephemeral=True
        )

    @app_commands.command(
        name="reset",
        description="Password-protected: delete an alliance."
    )
    async def reset(
        self,
        inter: discord.Interaction,
        alliance: str,
        password: str
    ):
        """
        /reset <alliance> <password>
        Deletes an alliance and all related data (members, colonies, etc.).
        Protected by the same admin password.
        """
        # 1) Verify password
        if password != ADMIN_PASS:
            return await inter.response.send_message(
                "❌ Bad password.", ephemeral=True
            )

        # 2) Ensure the alliance exists before deleting
        if not await alliance_exists(self.bot.pool, alliance):
            return await inter.response.send_message(
                "❌ Alliance not found.", ephemeral=True
            )

        # 3) Delete from the DB
        await self.bot.pool.execute(
            "DELETE FROM alliances WHERE name=$1",
            alliance
        )

        # 4) Confirm deletion
        await inter.response.send_message(
            "✅ Alliance deleted.", ephemeral=True
        )

# This setup function tells discord.py how to register this Cog
async def setup(bot):
    await bot.add_cog(AllianceCog(bot))
