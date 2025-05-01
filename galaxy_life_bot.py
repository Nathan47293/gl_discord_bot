# -*- coding: utf-8 -*-
"""
Galaxy Life Alliance Tracker Bot — *colonies‑only*  (fixed copy_global_to)
======================================================================
Discord slash‑command bot that tracks enemy alliances in Galaxy Life.

* ➖  **No main planet field** — each member just has up to 11 colony coordinates.
* 🛠️  **Bug‑fix**: switched `copy_global_to_guild()` → `copy_global_to()` for
  Discord .py ≥ 2.3 (prevents AttributeError seen on Railway).

Commands
--------
```
/addalliance name
/addmember alliance member
/addcolony alliance member x y
/show alliance
/list
/reset alliance   (admin‑only)
```

Set up exactly as before — just redeploy this file.

---
```python
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


async def respond(interaction: discord.Interaction, msg: str, /, *, ep: bool = True):
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=ep)
    else:
        await interaction.response.send_message(msg, ephemeral=ep)

# ---------------------------------------------------------------------------
# Autocomplete helpers
# ---------------------------------------------------------------------------
async def alliance_ac(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    cur_low = current.lower()
    return [
        app_commands.Choice(name=a["display_name"], value=a["display_name"])
        for a in alliances.values()
        if cur_low in a["display_name"].lower()
    ][:25]


def member_ac_factory(param_alliance: str):
    async def _ac(interaction: discord.Interaction, current: str):
        alliance_val = getattr(interaction.namespace, param_alliance, None)
        if not alliance_val:
            return []
        a = alliances.get(key(alliance_val))
        if not a:
            return []
        cur_low = current.lower()
        return [
            app_commands.Choice(name=m["display_name"], value=m["display_name"])
            for m in a["members"].values()
            if cur_low in m["display_name"].lower()
        ][:25]

    return _ac

# ---------------------------------------------------------------------------
# Discord bot setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()  # message_content not required

guild_id_env = os.getenv("TEST_GUILD_ID")
TEST_GUILD = discord.Object(int(guild_id_env)) if guild_id_env else None


class GalaxyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self):
        # Fast guild‑only sync when TEST_GUILD_ID is set
        if TEST_GUILD:
            # >>> FIX: use copy_global_to instead of copy_global_to_guild <<<
            self.tree.copy_global_to(guild=TEST_GUILD)
            await self.tree.sync(guild=TEST_GUILD)
            print(f"✓ Commands synced to test guild {TEST_GUILD.id}")
        else:
            await self.tree.sync()
            print("✓ Global slash‑commands synced (first time can take up to 1 h)")


bot = GalaxyBot()

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


@bot.tree.command(description="Add a member to an alliance.")
@app_commands.autocomplete(alliance=alliance_ac)
@app_commands.describe(alliance="Alliance name", member="Member/player name")
async def addmember(interaction: discord.Interaction, alliance: str, member: str):
    a = get_alliance(alliance)
    mk = key(member)
    if mk in a["members"]:
        await respond(interaction, f"**{member}** already exists in {a['display_name']}.")
        return
    a["members"][mk] = {"display_name": member, "colonies": []}
    save_data(alliances)
    await respond(interaction, f"Member **{member}** added to **{a['display_name']}**.")


@bot.tree.command(description="Add a colony (max 11 per member).")
@app_commands.autocomplete(
    alliance=alliance_ac, member=member_ac_factory("alliance")
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
        await respond(
            interaction,
            f"{m['display_name']} already has {MAX_COLONIES} colonies saved.",
        )
        return
    m["colonies"].append({"x": x, "y": y})
    save_data(alliances)
    await respond(
        interaction,
        f"Colony `{x},{y}` added for **{m['display_name']}** (
        {len(m['colonies'])}/{MAX_COLONIES}).",
    )


@bot.tree.command(name="show", description="Show an alliance’s members & colonies.")
@app_commands.autocomplete(alliance=alliance_ac)
async def show(interaction: discord.Interaction, alliance: str):
    a = get_alliance(alliance)
    embed = discord.Embed(title=a["display_name"], color=discord.Color.blue())
    if not a["members"]:
        embed.description = "_(no members saved yet)_"
    else:
        for m in a["members"].values():
            colonies = (
                ", ".join(f"`{c['x']},{c['y']}`" for c in m["colonies"]) or "None"
            )
            embed.add_field(
                name=f"{m['display_name']} ({len(m['colonies'])}/{MAX_COLONIES})",
                value=colonies,
                inline=False,
            )
    await interaction.response.send_message(embed=embed, ephemeral=False)


@bot.tree.command(description="List all alliances.")
async def list(interaction: discord.Interaction):
    if not alliances:
        await respond(interaction, "No alliances recorded yet.")
        return
    msg = "\n".join(f"- {a['display_name']}" for a in alliances.values())
    await interaction.response.send_message(
        f"**All Alliances ({len(alliances)})**\n{msg}", ephemeral=False
    )


@bot.tree.command(description="Delete an alliance (admin‑only).")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.autocomplete(alliance=alliance_ac)
async def reset(interaction: discord.Interaction, alliance: str):
    k = key(alliance)
    if k not in alliances:
        await respond(interaction, f"Alliance **{alliance}** not found.")
        return
    del alliances[k]
    save_data(alliances)
    await respond(interaction, f"Alliance **{alliance}** has been removed.")


@reset.error
async def reset_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await respond(interaction, "You need **Administrator** permission to do that.")
    else:
        raise error

# ---------------------------------------------------------------------------
# Run bot
# ---------------------------------------------------------------------------
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Set the DISCORD_BOT_TOKEN environment variable.")

bot.run(TOKEN)
```
