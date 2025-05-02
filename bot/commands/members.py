# bot/commands/members.py
from discord import app_commands
from discord.ext import commands

from ..db import alliance_exists, member_exists, set_main_sb

class MemberCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="addmember", description="Add a member to an alliance (with main SB).")
    @app_commands.autocomplete(alliance=lambda inter, cur: [
        app_commands.Choice(name=n, value=n)
        for n in (await all_alliances(self.bot.pool))
        if cur.lower() in n.lower()
    ])
    async def addmember(self, inter, alliance: str, member: str, main_sb: int):
        if not await alliance_exists(self.bot.pool, alliance):
            return await inter.response.send_message("❌ Alliance not found.", ephemeral=True)
        if await member_exists(self.bot.pool, alliance, member):
            return await inter.response.send_message("❌ Member exists.", ephemeral=True)
        await self.bot.pool.execute(
            "INSERT INTO members(alliance,member,main_sb) VALUES($1,$2,$3)",
            alliance, member, main_sb
        )
        await inter.response.send_message(f"✅ Added **{member}** (SB{main_sb}).", ephemeral=True)

    @app_commands.command(name="setmainsb", description="Update a member’s main starbase level.")
    @app_commands.autocomplete(alliance=lambda inter, cur: [
        app_commands.Choice(name=n, value=n)
        for n in (await all_alliances(self.bot.pool))
        if cur.lower() in n.lower()
    ])
    async def setmainsb(self, inter, alliance: str, member: str, sb: int):
        if not await member_exists(self.bot.pool, alliance, member):
            return await inter.response.send_message("❌ Member not found.", ephemeral=True)
        await set_main_sb(self.bot.pool, alliance, member, sb)
        await inter.response.send_message(f"✅ **{member}**’s main SB set to {sb}.", ephemeral=True)

    @app_commands.command(name="removemember", description="Remove a member (and all their colonies).")
    @app_commands.autocomplete(alliance=lambda inter, cur: [
        app_commands.Choice(name=n, value=n)
        for n in (await all_alliances(self.bot.pool))
        if cur.lower() in n.lower()
    ])
    async def removemember(self, inter, alliance: str, member: str):
        if not await member_exists(self.bot.pool, alliance, member):
            return await inter.response.send_message("❌ Member not found.", ephemeral=True)
        await self.bot.pool.execute(
            "DELETE FROM members WHERE alliance=$1 AND member=$2",
            alliance, member
        )
        await inter.response.send_message(f"✅ Removed **{member}**.", ephemeral=True)

    @app_commands.command(name="renamemember", description="Rename a member (keeps colonies).")
    @app_commands.autocomplete(alliance=lambda inter, cur: [
        app_commands.Choice(name=n, value=n)
        for n in (await all_alliances(self.bot.pool))
        if cur.lower() in n.lower()
    ])
    async def renamemember(self, inter, alliance: str, old: str, new: str):
        if not await member_exists(self.bot.pool, alliance, old):
            return await inter.response.send_message("❌ Original not found.", ephemeral=True)
        if await member_exists(self.bot.pool, alliance, new):
            return await inter.response.send_message("❌ New name taken.", ephemeral=True)
        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                main_sb = await conn.fetchval(
                    "SELECT main_sb FROM members WHERE alliance=$1 AND member=$2",
                    alliance, old
                ) or 0
                await conn.execute(
                    "INSERT INTO members(alliance,member,main_sb) VALUES($1,$2,$3)",
                    alliance, new, main_sb
                )
                await conn.execute(
                    "UPDATE colonies SET member=$1 WHERE alliance=$2 AND member=$3",
                    new, alliance, old
                )
                await conn.execute(
                    "DELETE FROM members WHERE alliance=$1 AND member=$2",
                    alliance, old
                )
        await inter.response.send_message(f"✅ Renamed **{old}** to **{new}**.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(MemberCog(bot))
