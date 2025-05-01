
\"\"\"
Galaxy Life Alliance Tracker Bot – per-member colonies
=====================================================
A Discord *slash-command* bot for tracking enemy alliances in **Galaxy Life**.

Data model
----------
Alliance → Members → each Member stores:
* `main_planet` (their home planet)
* up to **11 colony coordinates** `(x, y)`

Key slash commands
------------------
/addalliance name  – create an alliance
/addmember alliance member main_planet  – add a member
/addcolony alliance member x y  – add colony coords (max 11 per member)
/show alliance  – embed of all members & colonies
/list  – list alliances
/reset alliance  – delete (admin-only)

Dev hints
---------
* Set **TEST_GUILD_ID** env-var to your server ID for instant command registration.
* The bot token must be provided via the **DISCORD_BOT_TOKEN** environment variable.
\"\"\"

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

import discord
from discord import app_commands
from discord.ext import commands

DATA_FILE = "alliances.json"
MAX_COLONIES = 11

# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def load_data() -> Dict[str, Any]:
    if os.path.isfile(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as fp:
            return json.load(fp)
    return {}


def save_data(data: Dict[str, Any]) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2)


alliances: Dict[str, Any] = load_data()

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def key(name: str) -> str:  # normalise alliance / member keys
    return name.lower()


def get_alliance(name: str) -> Dict[str, Any]:
    k = key(name)
    if k not in alliances:
        raise app_commands.AppCommandError(f"Alliance '{name}' not found.")
    return alliances[k]


def get_member(a: Dict[str, Any], member_name: str) -> Dict[str, Any]:
    mk = key(member_name)
    if mk not in a["members"]:
        raise app_commands.AppCommandError(
            f"Member '{member_name}' not found in {a['display_name']}."
        )
    return a["members"][mk]


async def respond(interaction: discord.Interaction, msg: str, /, *, ephemeral: bool = True):
    \"\"\"Reply helper that works whether the initial response is sent or not.\"\"\"
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(msg, ephemeral=ephemeral)

# ---------------------------------------------------------------------------
# Autocomplete helpers
# ---------------------------------------------------------------------------

async def alliance_autocomplete(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    current_low = current.lower()
    return [
        app_commands.Choice(name=a["display_name"], value=a["display_name"])
        for a in alliances.values()
        if current_low in a["display_name"].lower()
    ][:25]


def member_autocomplete_factory(param_alliance_name: str):
    async def _ac(interaction: discord.Interaction, current: str):
        alliance_name = getattr(interaction.namespace, param_alliance_name, None)
        if not alliance_name:
            return []
        a = alliances.get(key(alliance_name))
        if not a:
            return []
        current_low = current.lower()
        return [
            app_commands.Choice(name=m["display_name"], value=m["display_name"])
            for m in a["members"].values()
            if current_low in m["display_name"].lower()
        ][:25]

    return _ac

# ---------------------------------------------------------------------------
# Discord bot setup
# ---------------------------------------------------------------------------

intents = discord.Intents.default()  # message content not needed for slash cmds

guild_id_env = os.getenv("TEST_GUILD_ID")
TEST_GUILD = discord.Object(int(guild_id_env)) if guild_id_env else None


class GalaxyLifeBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self) -> None:
        if TEST_GUILD:
            self.tree.copy_global_to_guild(TEST_GUILD)
            await self.tree.sync(guild=TEST_GUILD)
            print(f"✓ Commands synced to test guild {TEST_GUILD.id}")
        else:
            await self.tree.sync()
            print("✓ Global slash-commands synced (may take up to 60 min first time)")


bot = GalaxyLifeBot()

# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

@bot.tree.command(description="Create a new alliance entry.")
@app_commands.describe(name="Alliance name")
async def addalliance(interaction: discord.Interaction, name: str):
    k = key(name)
    if k in alliances:
        await respond(interaction, f"Alliance **{name}** already exists.")
        return
    alliances[k] = {"display_name": name, "members": {}}
    save_data(alliances)
    await respond(interaction, f"Alliance **{name}** registered! 🎯")


@bot.tree.command(description="Add a member to an alliance (with their main planet).")
@app_commands.autocomplete(alliance=alliance_autocomplete)
@app_commands.describe(
    alliance="Alliance name",
    member="Member/player name",
    main_planet="Their main planet",
)
async def addmember(
    interaction: discord.Interaction,
    alliance: str,
    member: str,
    main_planet: str,
):
    a = get_alliance(alliance)
    mk = key(member)
    if mk in a["members"]:
        await respond(interaction, f"**{member}** already exists in {a['display_name']}.")
        return
    a["members"][mk] = {
        "display_name": member,
        "main_planet": main_planet,
        "colonies": [],
    }
    save_data(alliances)
    await respond(interaction, f"Member **{member}** added to **{a['display_name']}**.")


@bot.tree.command(description="Add a colony coordinate to a member (max 11 each).")
@app_commands.autocomplete(
    alliance=alliance_autocomplete,
    member=member_autocomplete_factory("alliance"),
)
@app_commands.describe(
    alliance="Alliance name",
    member="Member/player name",
    x="X coordinate",
    y="Y coordinate",
)
async def addcolony(
    interaction: discord.Interaction,
    alliance: str,
    member: str,
    x: int,
    y: int,
):
    a = get_alliance(alliance)
    m = get_member(a, member)
    if len(m["colonies"]) >= MAX_COLONIES:
        await respond(interaction, f"{m['display_name']} already has {MAX_COLONIES} colonies saved.")
        return
    m["colonies"].append({\"x\": x, \"y\": y})
    save_data(alliances)
    await respond(
        interaction,
        f\"Colony `{x},{y}` added for **{m['display_name']}** ({len(m['colonies'])}/{MAX_COLONIES}).\",
    )


@bot.tree.command(name="show", description="Show all members & colonies of an alliance.")
@app_commands.autocomplete(alliance=alliance_autocomplete)
async def show(interaction: discord.Interaction, alliance: str):
    a = get_alliance(alliance)
    embed = discord.Embed(title=a["display_name"], color=discord.Color.blue())
    if not a["members"]:
        embed.description = \"_(no members saved yet)_\"
    else:
        for m in a["members"].values():
            colonies = ", ".join(f\"`{c['x']},{c['y']}`\" for c in m["colonies"]) or "None"
            embed.add_field(
                name=f\"{m['display_name']} – {m['main_planet']} ({len(m['colonies'])}/{MAX_COLONIES})\",
                value=colonies,
                inline=False,
            )
    await interaction.response.send_message(embed=embed, ephemeral=False)


@bot.tree.command(description="Quick list of recorded alliances.")
async def list(interaction: discord.Interaction):
    if not alliances:
        await respond(interaction, "No alliances recorded yet.")
        return
    msg = "\\n".join(f\"- {a['display_name']}\" for a in alliances.values())
    await interaction.response.send_message(f\"**All Alliances ({len(alliances)})**\\n{msg}\", ephemeral=False)


@bot.tree.command(description="Delete an alliance (admin-only).")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.autocomplete(alliance=alliance_autocomplete)
async def reset(interaction: discord.Interaction, alliance: str):
    k = key(alliance)
    if k not in alliances:
        await respond(interaction, f\"Alliance **{alliance}** not found.\")
        return
    del alliances[k]
    save_data(alliances)
    await respond(interaction, f\"Alliance **{alliance}** has been removed.\")


@reset.error  # noqa: WPS437
async def reset_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await respond(interaction, "You need **Administrator** permission to do that.")
    else:
        raise error

# ---------------------------------------------------------------------------
# Run the bot
# ---------------------------------------------------------------------------
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Set the DISCORD_BOT_TOKEN environment variable.")

bot.run(TOKEN)
