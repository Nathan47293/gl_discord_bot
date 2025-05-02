# bot/commands/alliances.py
from discord import app_commands
from discord.ext import commands

from ..db import alliance_exists, all_alliances, set_active_alliance, ADMIN_PASS

class AllianceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="addalliance", description="Create a new alliance.")
    async def addalliance(self, inter, name: str):
        if await alliance_exists(self.bot.pool, name):
            return await inter.response.send_message("❌ Already exists.", ephemeral=True)
        await self.bot.pool.execute("INSERT INTO alliances(name) VALUES($1)", name)
        await inter.response.send_message(f"✅ Alliance **{name}** created.", ephemeral=True)

    @app_commands.command(name="list", description="List all alliances.")
    async def list_all(self, inter):
        opts = await all_alliances(self.bot.pool)
        if not opts:
            return await inter.response.send_message("❌ No alliances recorded.", ephemeral=True)
        await inter.response.send_message("\n".join(f"- {o}" for o in opts))

    @app_commands.command(name="setalliance", description="Password-protected: set this guild’s alliance.")
    async def setalliance(self, inter, alliance: str, password: str):
        if password != ADMIN_PASS:
            return await inter.response.send_message("❌ Bad password.", ephemeral=True)
        if not await alliance_exists(self.bot.pool, alliance):
            return await inter.response.send_message("❌ Alliance not found.", ephemeral=True)
        await set_active_alliance(self.bot.pool, str(inter.guild_id), alliance)
        await inter.response.send_message(f"✅ Active alliance set to **{alliance}**.", ephemeral=True)

    @app_commands.command(name="reset", description="Password-protected: delete an alliance.")
    async def reset(self, inter, alliance: str, password: str):
        if password != ADMIN_PASS:
            return await inter.response.send_message("❌ Bad password.", ephemeral=True)
        if not await alliance_exists(self.bot.pool, alliance):
            return await inter.response.send_message("❌ Alliance not found.", ephemeral=True)
        await self.bot.pool.execute("DELETE FROM alliances WHERE name=$1", alliance)
        await inter.response.send_message("✅ Alliance deleted.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(AllianceCog(bot))
