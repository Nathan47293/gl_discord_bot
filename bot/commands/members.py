# bot/commands/members.py
import discord
from discord import app_commands
from discord.ext import commands

from ..db import alliance_exists, member_exists, set_main_sb, all_alliances

class MemberCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Autocomplete for alliance names
    async def alliance_autocomplete(
        self, inter: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        choices = await all_alliances(self.bot.pool)
        low = current.lower()
        return [
            app_commands.Choice(name=a, value=a)
            for a in choices
            if low in a.lower()
        ][:25]

    # Autocomplete for member names, given an alliance
    async def member_autocomplete(
        self, inter: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        alliance = inter.namespace.alliance
        if not alliance:
            return []
        rows = await self.bot.pool.fetch(
            "SELECT member FROM members WHERE alliance=$1 ORDER BY member",
            alliance
        )
        low = current.lower()
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
        if not await alliance_exists(self.bot.pool, alliance):
            return await inter.response.send_message("❌ Alliance not found.", ephemeral=True)
        if await member_exists(self.bot.pool, alliance, member):
            return await inter.response.send_message("❌ Member exists.", ephemeral=True)
        await self.bot.pool.execute(
            "INSERT INTO members(alliance,member,main_sb) VALUES($1,$2,$3)",
            alliance, member, main_sb
        )
        await inter.response.send_message(f"✅ Added **{member}** (SB{main_sb}).", ephemeral=True)

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
        sb: app_commands.Range[int, 1, 9]
    ):
        if not await member_exists(self.bot.pool, alliance, member):
            return await inter.response.send_message("❌ Member not found.", ephemeral=True)
        await set_main_sb(self.bot.pool, alliance, member, sb)
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
        member: str
    ):
        if not await member_exists(self.bot.pool, alliance, member):
            return await inter.response.send_message("❌ Member not found.", ephemeral=True)
        await self.bot.pool.execute(
            "DELETE FROM members WHERE alliance=$1 AND member=$2",
            alliance, member
        )
        await inter.response.send_message(f"✅ Removed **{member}**.", ephemeral=True)

    @app_commands.command(
        name="renamemember",
        description="Rename a member (keeps colonies)."
    )
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    @app_commands.autocomplete(old=member_autocomplete)  # ← here we autocomplete 'old', not 'member'
    async def renamemember(
        self,
        inter: discord.Interaction,
        alliance: str,
        old: str,   # the original member name
        new: str    # the new member name
    ):
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
