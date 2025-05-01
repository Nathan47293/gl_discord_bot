# -*- coding: utf-8 -*-
"""
Galaxy Life Alliance Tracker Bot – colonies only (ASCII‑safe)
============================================================
Discord slash‑command bot that tracks enemy alliances in Galaxy Life.

* No main planet field — each member can have up to 11 colony coordinates.
* Fixed: uses copy_global_to for discord.py ≥ 2.3.

Commands
--------
/addalliance name
/addmember alliance member
/addcolony alliance member x y
/show alliance
/list
/reset alliance (admin only)
"""

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

def key(name: str) -> str:
    """Normalize to lower‑case so look‑ups are case‑insensitive."""
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


async def respond(inter: discord.Interaction, msg: str, *, ep: bool = True):
    if inter.response.is_done():
        await inter.followup.send(msg, ephemeral=ep)
    else:
        await inter.response.send_message(msg, ephemeral=ep)

# ---------------------------------------------------------------------------
# Autocomplete helpers
# ---------------------------------------------------------------------------
async def alliance_ac(inter: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    cur = current.lower()
    return [
        app_commands.Choice(name=a["display_name"], value=a["display_name"])
        for a in alliances.values()
        if cur in a["display_name"].lower()
    ][:25]


def member_ac_factory(param_alliance: str):
    async def _ac(inter: discord.Interaction, current: str):
        alliance_val = getattr(inter.namespace, param_alliance, None)
        if not alliance_val:
            return []
        a = alliances.get(key(alliance_val))
        if not a:
            return []
        cur = current.lower()
        return [
            app_commands.Choice(name=m["display_name"], value=m["display_name"])
            for m in a["members"].values()
            if cur in m["display_name"].lower()
        ][:25]
    return _ac

# ---------------------------------------------------------------------------
# Discord bot setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()

TEST_GUILD = None
if "TEST_GUILD_ID" in os.environ:
    try:
        TEST_GUILD = discord.Object(int(os.environ["TEST_GUILD_ID"]))
    except ValueError:
        print("TEST_GUILD_ID env var must be an integer guild id")

class GalaxyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self):
        if TEST_GUILD is not None:
            self.tree.copy_global_to(guild=TEST_GUILD)
            await self.tree.sync(guild=TEST_GUILD)
            print(f"Commands synced to test guild {TEST_GUILD.id}")
        else:
            await self.tree.sync()
            print("Global slash‑commands synced (first time can take up to an hour)")

bot = GalaxyBot()

# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------
@bot.tree.command(description="Create a new alliance entry.")
@app_commands.describe(name="Alliance name")
async def addalliance(inter: discord.Interaction, name: str):
    k = key(name)
    if k in alliances:
        await respond(inter, f"Alliance **{name}** already exists.")
        return
    alliances[k] = {"display_name": name, "members": {}}
    save_data(alliances)
    await respond(inter, f"Alliance **{name}** registered!")


@bot.tree.command(description="Add a member to an alliance.")
@app_commands.autocomplete(alliance=alliance_ac)
@app_commands.describe(alliance="Alliance name", member="Member name")
async def addmember(inter: discord.Interaction, alliance: str, member: str):
    a = get_alliance(alliance)
    mk = key(member)
    if mk in a["members"]:
        await respond(inter, f"{member} already exists in {a['display_name']}.")
        return
    a["members"][mk] = {"display_name": member, "colonies": []}
    save_data(alliances)
    await respond(inter, f"Member {member} added.")


@bot.tree.command(description="Add a colony coordinate to a member (max 11).")
@app_commands.autocomplete(alliance=alliance_ac, member=member_ac_factory("alliance"))
@app_commands.describe(alliance="Alliance", member="Member", x="X coord", y="Y coord")
async def addcolony(inter: discord.Interaction, alliance: str, member: str, x: int, y: int):
    a = get_alliance(alliance)
    m = get_member(a, member)
    if len(m["colonies"]) >= MAX_COLONIES:
        await respond(inter, f"{m['display_name']} already has {MAX_COLONIES} colonies recorded.")
        return
    m["colonies"].append({"x": x, "y": y})
    save_data(alliances)
    await respond(inter, f"Colony {x},{y} added for {m['display_name']} ({len(m['colonies'])}/{MAX_COLONIES}).")


@bot.tree.command(description="Show an alliance’s members and colonies.")
@app_commands.autocomplete(alliance=alliance_ac)
async def show(inter: discord.Interaction, alliance: str):
    a = get_alliance(alliance)
    embed = discord.Embed(title=a["display_name"], color=discord.Color.blue())
    if not a["members"]:
        embed.description = "No members recorded."
    else:
        for m in a["members"].values():
            colonies = ", ".join(f"{c['x']},{c['y']}" for c in m["colonies"]) or "None"
            embed.add_field(name=f"{m['display_name']} ({len(m['colonies'])}/{MAX_COLONIES})", value=colonies, inline=False)
    await inter.response.send_message(embed=embed, ephemeral=False)


@bot.tree.command(description="List all alliances.")
async def list(inter: discord.Interaction):
    if not alliances:
        await respond(inter, "No alliances recorded.")
        return
    msg = "\n".join(f"- {a['display_name']}" for a in alliances.values())
    await inter.response.send_message(f"All Alliances ({len(alliances)})\n{msg}", ephemeral=False)


@bot.tree.command(description="Delete an alliance (admin only).")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.autocomplete(alliance=alliance_ac)
async def reset(inter: discord.Interaction, alliance: str):
    k = key(alliance)
    if k not in alliances:
        await respond(inter, f"Alliance {alliance} not found.")
        return
    del alliances[k]
    save_data(alliances)
    await respond(inter, f"Alliance {alliance} deleted.")

@reset.error
async def reset_error(inter: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await respond(inter, "Administrator permission required.")
    else:
        raise error

# ---------------------------------------------------------------------------
# Run bot
# ---------------------------------------------------------------------------
TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Set the DISCORD_BOT_TOKEN env var.")

bot
