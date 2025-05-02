# bot/commands/colonies.py
import discord
from discord import app_commands
from discord.ext import commands

from ..db import (
    member_exists,
    colony_count,
    get_members_with_colonies,
    all_alliances,
    MAX_COLONIES,
    MAX_MEMBERS,
)

class ColonyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def alliance_autocomplete(self, inter: discord.Interaction, current: str):
        choices = await all_alliances(self.bot.pool)
        low = current.lower()
        return [
            app_commands.Choice(name=a, value=a)
            for a in choices
            if low in a.lower()
        ][:25]

    async def member_autocomplete(self, inter: discord.Interaction, current: str):
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
        if not await member_exists(self.bot.pool, alliance, member):
            return await inter.response.send_message("❌ Member not found.", ephemeral=True)
        if await colony_count(self.bot.pool, alliance, member) >= MAX_COLONIES:
            return await inter.response.send_message("❌ Max colonies reached.", ephemeral=True)
        await self.bot.pool.execute(
            "INSERT INTO colonies(alliance,member,starbase,x,y) VALUES($1,$2,$3,$4,$5)",
            alliance, member, starbase, x, y
        )
        await inter.response.send_message(f"✅ Colony SB{starbase} ({x},{y}) added.", ephemeral=True)

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
        if not await member_exists(self.bot.pool, alliance, member):
            return await inter.response.send_message("❌ Member not found.", ephemeral=True)
        res = await self.bot.pool.execute(
            "DELETE FROM colonies WHERE alliance=$1 AND member=$2 AND starbase=$3 AND x=$4 AND y=$5",
            alliance, member, starbase, x, y
        )
        if res.endswith("0"):
            return await inter.response.send_message("❌ No such colony.", ephemeral=True)
        await inter.response.send_message(f"✅ Removed SB{starbase} ({x},{y}).", ephemeral=True)

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
        data = await get_members_with_colonies(self.bot.pool, alliance)
        embed = discord.Embed(
            title=f"{alliance} ({len(data)}/{MAX_MEMBERS} members)",
            color=discord.Color.blue()
        )
        total_cols = 0
        for member, cnt, cols, msb in data:
            total_cols += cnt
            header = f"{member} (SB{msb} — {cnt}/{MAX_COLONIES})"
            if not cols:
                embed.add_field(name=header, value="—", inline=False)
            else:
                lines = "\n".join(f"SB{sb} ({xx},{yy})" for sb, xx, yy in cols)
                embed.add_field(name=header, value=lines, inline=False)
        embed.set_footer(text=f"{total_cols} colonies discovered")
        await inter.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(ColonyCog(bot))
