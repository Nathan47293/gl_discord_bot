# bot/commands/colonies.py
from discord import app_commands
from discord.ext import commands

from ..db import member_exists, colony_count, get_members_with_colonies

class ColonyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="addcolony", description="Add a colony coordinate (max 11 per member).")
    @app_commands.autocomplete(alliance=lambda inter, cur: [
        app_commands.Choice(name=n, value=n)
        for n in (await all_alliances(self.bot.pool))
        if cur.lower() in n.lower()
    ])
    async def addcolony(self, inter, alliance: str, member: str, starbase: int, x: int, y: int):
        if not await member_exists(self.bot.pool, alliance, member):
            return await inter.response.send_message("❌ Member not found.", ephemeral=True)
        if await colony_count(self.bot.pool, alliance, member) >= MAX_COLONIES:
            return await inter.response.send_message("❌ Max colonies reached.", ephemeral=True)
        await self.bot.pool.execute(
            "INSERT INTO colonies(alliance,member,starbase,x,y) VALUES($1,$2,$3,$4,$5)",
            alliance, member, starbase, x, y
        )
        await inter.response.send_message(f"✅ Colony SB{starbase} ({x},{y}) added.", ephemeral=True)

    @app_commands.command(name="removecolony", description="Remove a specific colony.")
    @app_commands.autocomplete(alliance=lambda inter, cur: [
        app_commands.Choice(name=n, value=n)
        for n in (await all_alliances(self.bot.pool))
        if cur.lower() in n.lower()
    ])
    async def removecolony(self, inter, alliance: str, member: str, starbase: int, x: int, y: int):
        if not await member_exists(self.bot.pool, alliance, member):
            return await inter.response.send_message("❌ Member not found.", ephemeral=True)
        res = await self.bot.pool.execute(
            "DELETE FROM colonies WHERE alliance=$1 AND member=$2 AND starbase=$3 AND x=$4 AND y=$5",
            alliance, member, starbase, x, y
        )
        if res.endswith("0"):
            return await inter.response.send_message("❌ No such colony.", ephemeral=True)
        await inter.response.send_message(f"✅ Removed SB{starbase} ({x},{y}).", ephemeral=True)

    @app_commands.command(name="show", description="Show an alliance’s members & colonies.")
    @app_commands.autocomplete(alliance=lambda inter, cur: [
        app_commands.Choice(name=n, value=n)
        for n in (await all_alliances(self.bot.pool))
        if cur.lower() in n.lower()
    ])
    async def show(self, inter, alliance: str):
        if not await alliance_exists(self.bot.pool, alliance):
            return await inter.response.send_message("❌ Alliance not found.", ephemeral=True)
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
