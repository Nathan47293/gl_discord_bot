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

# Configure the Discord Gateway intents your bot needs
intents = discord.Intents.default()
intents.message_content = True  # Enable message content intent
intents.members = True          # Enable server members intent

# Define default permissions the bot needs
default_permissions = discord.Permissions(
    send_messages=True,
    view_channel=True,
    read_message_history=True,
    embed_links=True,
    external_emojis=True,
    add_reactions=True,
    manage_messages=True  # Needed for editing/deleting messages
)

# Add allowed channel ID
ALLOWED_CHANNEL = 1330260201118371872

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
        #  - default_permissions: the permissions the bot needs
        super().__init__(command_prefix="!", intents=intents, help_command=None, default_permissions=default_permissions)

        # These attributes will be populated in setup_hook:
        self.pool        = None          # AsyncPG pool for DB queries
        self.TOKEN       = TOKEN         # Bot token for .run()
        self.ADMIN_PASS  = ADMIN_PASS    # Global admin password for protected commands

    async def setup_hook(self):
        """Discord.py calls this coroutine before login."""
        self.pool = await init_db_pool(DATABASE)
        await register_commands(self)

        # Set guild_only flag on all commands
        for cmd in self.tree.get_commands():
            cmd.guild_only = True
            if hasattr(cmd, 'children'):
                for child in cmd.children:
                    child.guild_only = True

        # Clear ALL commands first to prevent conflicts
        await self.tree.sync()

        if TEST_GUILD:
            guild = discord.Object(id=int(TEST_GUILD))
            # First clear any existing commands in both global and test guild
            await self.tree.sync()  # Clear global commands
            self.tree.clear_commands(guild=guild)  # Clear test guild commands
            
            # Now set up test guild commands
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            
            # Finally, clear global commands again
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
            print(f"❇ Commands synced ONLY to test guild {TEST_GUILD}")
        else:
            # Global mode: Just sync once with guild_only flag
            await self.tree.sync()
            print("✅ Global commands synced (guild-only)")

    async def on_interaction(self, interaction: discord.Interaction):
        """Global check that prevents DM interactions and wrong channels"""
        if interaction.guild_id is None:
            try:
                await interaction.response.send_message(
                    "❌ This bot can only be used in servers!", 
                    ephemeral=True
                )
            except:
                pass
            return

        # Check for correct channel
        if interaction.channel_id != ALLOWED_CHANNEL:
            try:
                await interaction.response.send_message(
                    "❌ Please use bot commands in the designated channel.", 
                    ephemeral=True
                )
            except:
                pass
            return

        return

# ─────────────────────────────────────────────────────────────────────────────
# Instantiate and run the bot
# ─────────────────────────────────────────────────────────────────────────────

# Create exactly one instance of your bot
bot = GalaxyBot()
