# bot/core.py

import os
import discord
from discord.ext import commands

# Import your database initializer and admin password constant
from .db import init_db_pool, ADMIN_PASS
# Import the function that registers all your command cogs
from .commands import register_commands

# ─────────────────────────────────────────────────────────────────────────────
# Configuration: read from environment variables
# ─────────────────────────────────────────────────────────────────────────────

# The Discord bot token used to authenticate with the Discord API
TOKEN      = os.getenv("DISCORD_BOT_TOKEN")
# The PostgreSQL connection URL for AsyncPG
DATABASE   = os.getenv("DATABASE_URL")
# Optional: a specific guild ID (server) to sync commands to for faster testing
TEST_GUILD = os.getenv("TEST_GUILD_ID")

# Ensure the essential variables are provided, otherwise crash early:
if not TOKEN:
    raise RuntimeError("Set DISCORD_BOT_TOKEN")
if not DATABASE:
    raise RuntimeError("Set DATABASE_URL")

# Configure the Discord Gateway intents your bot needs.
# `default()` gives you non-privileged intents (guilds, members, messages, etc.),
# without message content. If you need message content, you'd enable it here.
intents = discord.Intents.default()


# ─────────────────────────────────────────────────────────────────────────────
# Main Bot Class Definition
# ─────────────────────────────────────────────────────────────────────────────

class GalaxyBot(commands.Bot):
    """
    A subclass of commands.Bot that:
      - Holds a reference to the database pool
      - Loads and syncs all slash-command Cogs on startup
    """
    def __init__(self):
        # Call the base constructor:
        #  - command_prefix: only relevant for legacy text commands (we're using slash commands)
        #  - intents: which events the bot will receive
        #  - help_command=None: disable the default '!help' so we can provide our own or none
        super().__init__(command_prefix="!", intents=intents, help_command=None)

        # These attributes will be populated in setup_hook:
        self.pool        = None          # AsyncPG pool for DB queries
        self.TOKEN       = TOKEN         # Bot token for .run()
        self.ADMIN_PASS  = ADMIN_PASS    # Global admin password for protected commands

    async def setup_hook(self):
        """
        Discord.py calls this coroutine *before* logging in.
        Use it to do one-time setup:
          1) Initialize the database (connection pool + schema).
          2) Dynamically load all command Cogs.
          3) Sync your slash commands to Discord (either test guild or globally).
        """
        # 1) Initialize the AsyncPG pool and ensure your tables exist
        #    `init_db_pool` should create your tables if they don't exist already.
        self.pool = await init_db_pool(DATABASE)

        # 2) Load and register all command modules (Cogs) with this bot
        await register_commands(self)

        # 3) Sync slash commands to Discord
        if TEST_GUILD:
            # If TEST_GUILD_ID is set, we sync commands only to that guild.
            # This propagates changes instantly for testing.
            guild = discord.Object(id=int(TEST_GUILD))

            # Clear any existing commands in this guild (to avoid duplicates)
            self.tree.clear_commands(guild=guild)
            # Copy the global commands to this test guild
            self.tree.copy_global_to(guild=guild)
            # Perform the sync
            await self.tree.sync(guild=guild)
            print(f"❇ Commands synced to test guild {TEST_GUILD}")
        else:
            # No test guild specified: sync globally.
            # Global updates can take up to an hour to propagate on Discord’s side.
            await self.tree.sync()
            print("✅ Global commands synced")


# ─────────────────────────────────────────────────────────────────────────────
# Instantiate and run the bot
# ─────────────────────────────────────────────────────────────────────────────

# Create exactly one instance of your bot
bot = GalaxyBot()

# If this file is your entry point (e.g. python galaxy_life_bot.py),
# you’d typically do:
# 
# if __name__ == "__main__":
#     bot.run(bot.TOKEN)
#
# But if you import `bot` elsewhere, just call `bot.run(bot.TOKEN)` there.
