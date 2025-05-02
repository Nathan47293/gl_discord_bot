# bot/core.py
import os
import discord
from discord.ext import commands

from .db import init_db_pool, ADMIN_PASS
from .commands import register_commands

TOKEN      = os.getenv("DISCORD_BOT_TOKEN")
DATABASE   = os.getenv("DATABASE_URL")
TEST_GUILD = os.getenv("TEST_GUILD_ID")

if not TOKEN:
    raise RuntimeError("Set DISCORD_BOT_TOKEN")
if not DATABASE:
    raise RuntimeError("Set DATABASE_URL")

intents = discord.Intents.default()

class GalaxyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.pool = None
        self.TOKEN = TOKEN
        self.ADMIN_PASS = ADMIN_PASS

    async def setup_hook(self):
        # initialize DB (creates schema)
        self.pool = await init_db_pool(DATABASE)
        # register all cogs
        await register_commands(self)
        # sync slash commands
        if TEST_GUILD:
            guild = discord.Object(int(TEST_GUILD))
            self.tree.clear_commands(guild=guild)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print(f"❇ Commands synced to test guild {TEST_GUILD}")
        else:
            await self.tree.sync()
            print("✅ Global commands synced")

bot = GalaxyBot()
