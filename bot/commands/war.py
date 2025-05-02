# bot/commands/war.py
import discord
from discord import app_commands

from ..db import (
    alliance_exists, get_active_alliance, get_current_war,
    set_active_alliance
)
from ..views import WarView

class WarCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="attack", description="Start a war against an enemy alliance.")
    @app_commands.autocomplete(target=lambda inter,cur: [
        app_commands.Choice(name=n, value=n)
        for n in (await all_alliances(self.bot.pool))
        if cur.lower() in n.lower()
    ])
    async def attack(self, inter, target: str):
        gid = str(inter.guild_id)
        if await get_current_war(self.bot.pool, gid):
            return await inter.response.send_message("❌ War in progress—use /war.", ephemeral=True)
        if not await alliance_exists(self.bot.pool, target):
            return await inter.response.send_message("❌ Enemy alliance not found.", ephemeral=True)
        await self.bot.pool.execute(
            "INSERT INTO wars(guild_id, enemy_alliance) VALUES($1,$2)", gid, target
        )
        await _show_war(inter)

    @app_commands.command(name="war", description="Show the current war and attack buttons.")
    async def war(self, inter):
        if not await get_current_war(self.bot.pool, str(inter.guild_id)):
            return await inter.response.send_message("❌ No war in progress.", ephemeral=True)
        await _show_war(inter)

    @app_commands.command(name="endwar", description="Password-protected: end the current war.")
    async def endwar(self, inter, password: str):
        if password != self.bot.ADMIN_PASS:
            return await inter.response.send_message("❌ Bad password.", ephemeral=True)
        if not await get_current_war(self.bot.pool, str(inter.guild_id)):
            return await inter.response.send_message("❌ No war to end.", ephemeral=True)
        await self.bot.pool.execute("DELETE FROM wars WHERE guild_id=$1", str(inter.guild_id))
        await inter.response.send_message("✅ War ended.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(WarCog(bot))
