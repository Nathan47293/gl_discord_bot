# bot/views.py

import datetime
import math
from discord import ButtonStyle, ui

# Import helper to fetch the current war state
from .db import get_current_war

class WarView(ui.View):
    """
    A Discord UI View that renders a button for each member of the enemy alliance.
    Each button tracks the "attacked" cooldown, disabling itself and showing a timer
    if the member has been recently attacked.
    """
    # Updated __init__ to remove attacker parameter and add a task handle
    def __init__(self, guild_id: str, cooldown_hours: int, pool):
        """
        :param guild_id: Discord guild (server) ID as string
        :param cooldown_hours: The number of hours of cooldown after an attack
        :param pool: asyncpg Pool instance for DB operations
        """
        # Initialize a persistent View (timeout=None means it never times out)
        super().__init__(timeout=None)
        self.guild_id = guild_id      # Store guild context
        self.cd = cooldown_hours      # Store cooldown duration
        self.pool = pool              # DB pool for fetching records
        self._countdown_task = None
        self.current_page = 0
        self.mode = "main"      # "main" for members, "colony" for colonies
        self.colonies = []      # cache for colony mode
        self.members = []       # cache for main mode

    async def populate(self):
        try:
            if self.mode == "main":
                if not self.members:
                    war = await get_current_war(self.pool, self.guild_id)
                    if war:
                        enemy = war["enemy_alliance"]
                    elif hasattr(self, "enemy_alliance"):
                        enemy = self.enemy_alliance
                    else:
                        raise ValueError("No active war found for this guild.")
                    members_data = await self.pool.fetch(
                        "SELECT member, main_sb FROM members WHERE alliance=$1 ORDER BY main_sb DESC",
                        enemy
                    )
                    self.max_name_length = max((len(f"{m['member']} SB{m['main_sb']}") for m in members_data), default=0)
                    self.members = []
                    for rec in members_data:
                        m_name = rec["member"]
                        last = await self.pool.fetchval(
                            "SELECT last_attack FROM war_attacks WHERE guild_id=$1 AND member=$2",
                            self.guild_id, m_name
                        )
                        self.members.append({
                            "name": m_name,
                            "last": last,
                            "main_sb": rec["main_sb"]
                        })
                else:
                    for member in self.members:
                        member["last"] = await self.pool.fetchval(
                            "SELECT last_attack FROM war_attacks WHERE guild_id=$1 AND member=$2",
                            self.guild_id, member["name"]
                        )
            elif self.mode == "colony":
                if not self.colonies:
                    war = await get_current_war(self.pool, self.guild_id)
                    if war:
                        enemy = war["enemy_alliance"]
                    elif hasattr(self, "enemy_alliance"):
                        enemy = self.enemy_alliance
                    else:
                        raise ValueError("No active war found for this guild.")
                    # Query all colonies for enemy alliance sorted by starbase descending.
                    colonies_data = await self.pool.fetch(
                        "SELECT id, starbase, x, y FROM colonies WHERE alliance=$1 ORDER BY starbase DESC, x, y",
                        enemy
                    )
                    self.max_name_length = max((len(f"SB{c['starbase']} ({c['x']},{c['y']})") for c in colonies_data), default=0)
                    self.colonies = []
                    for rec in colonies_data:
                        ident = f"colony:{rec['id']}"
                        last = await self.pool.fetchval(
                            "SELECT last_attack FROM war_attacks WHERE guild_id=$1 AND member=$2",
                            self.guild_id, ident
                        )
                        self.colonies.append({
                            "ident": ident,
                            "starbase": rec["starbase"],
                            "x": rec["x"],
                            "y": rec["y"],
                            "last": last
                        })
                else:
                    for colony in self.colonies:
                        colony["last"] = await self.pool.fetchval(
                            "SELECT last_attack FROM war_attacks WHERE guild_id=$1 AND member=$2",
                            self.guild_id, colony["ident"]
                        )
            self.rebuild_view()
        except Exception as e:
            print(f"Error populating WarView: {e}")

    def rebuild_view(self):
        # Rebuild pagination and grid from cached self.members without DB queries.
        now = datetime.datetime.now(datetime.timezone.utc)
        # Choose cache based on mode.
        cache = self.members if self.mode=="main" else self.colonies
        members_per_column = 4
        columns = 2
        page_size = members_per_column * columns
        total_pages = math.ceil(len(cache) / page_size) if cache else 1
        if self.current_page < 0:
            self.current_page = 0
        elif self.current_page >= total_pages:
            self.current_page = total_pages - 1

        self.clear_items()
        # Pagination controls:
        items = []
        if self.current_page > 0:
            prev_btn = ui.Button(
                label="Previous",
                style=ButtonStyle.primary,
                custom_id="pagination:prev"
            )
            async def prev_page(interaction):
                await interaction.response.defer()
                self.current_page -= 1
                self.rebuild_view()
                await interaction.edit_original_response(view=self)
            prev_btn.callback = prev_page
            items.append(prev_btn)
        tracker_btn = ui.Button(
            label=f"Page {self.current_page+1}/{total_pages}",
            style=ButtonStyle.secondary,
            custom_id="pagination:tracker",
            disabled=True
        )
        items.append(tracker_btn)
        if self.current_page < total_pages - 1:
            next_btn = ui.Button(
                label="Next",
                style=ButtonStyle.primary,
                custom_id="pagination:next"
            )
            async def next_page(interaction):
                await interaction.response.defer()
                self.current_page += 1
                self.rebuild_view()
                await interaction.edit_original_response(view=self)
            next_btn.callback = next_page
            items.append(next_btn)
        refresh_btn = ui.Button(
            label="Refresh",
            style=ButtonStyle.secondary,
            custom_id="pagination:refresh"
        )
        async def refresh_page(interaction):
            await interaction.response.defer()
            await self.populate()
            await interaction.edit_original_response(view=self)
        refresh_btn.callback = refresh_page
        items.append(refresh_btn)
        # Mode switch button.
        if self.mode == "main":
            mode_btn = ui.Button(label="Colonies", style=ButtonStyle.primary, custom_id="mode:colonies")
            async def switch_to_colony(interaction):
                await interaction.response.defer()
                self.mode = "colony"
                self.current_page = 0
                if not self.colonies:
                    # Disable all buttons but only change Colonies label to Loading
                    for item in self.children:
                        item.disabled = True
                        if item.custom_id == "mode:colonies":
                            item.label = "Loading..."
                    await interaction.edit_original_response(view=self)
                    await self.populate()
                else:
                    self.rebuild_view()
                await interaction.edit_original_response(view=self)
            mode_btn.callback = switch_to_colony
        else:
            mode_btn = ui.Button(label="Main Planets", style=ButtonStyle.primary, custom_id="mode:main")
            async def switch_to_main(interaction):
                await interaction.response.defer()
                self.mode = "main"
                self.current_page = 0
                if not self.members:
                    await self.populate()
                else:
                    self.rebuild_view()
                await interaction.edit_original_response(view=self)
            mode_btn.callback = switch_to_main
        items.append(mode_btn)

        for btn in items:
            btn.row = 0
            self.add_item(btn)

        # Build member grid (same as before using cached self.members):
        for r in range(members_per_column):
            for c in range(columns):
                idx = self.current_page * page_size + (c * members_per_column + r)
                if idx >= len(cache):
                    continue
                entry = cache[idx]
                if self.mode == "main":
                    label = f"{entry['name']} SB{entry['main_sb']}"
                    custom_id_prefix = "war_atk:"
                    callback_func = self.create_callback(entry["name"])
                else:
                    label = f"SB{entry['starbase']} ({entry['x']},{entry['y']})"
                    custom_id_prefix = "war_col_atk:"
                    callback_func = self.create_colony_callback(entry["ident"])
                # Name button remains unchanged
                name_btn = ui.Button(
                    label=label,
                    style=ButtonStyle.secondary,
                    custom_id=f"label:{idx}",
                    disabled=True,
                    row=r+1
                )
                # For attack button, use member name if in main mode
                if self.mode == "main":
                    attack_custom_id = f"{custom_id_prefix}{entry['name']}"
                else:
                    attack_custom_id = f"{custom_id_prefix}{entry['ident']}"
                if entry["last"]:
                    elapsed = (now - entry["last"]).total_seconds()
                    remaining = max(0, self.cd * 3600 - elapsed)
                    if remaining >= 3600:
                        hr = int(remaining // 3600)
                        mn = int((remaining % 3600) // 60)
                        attack_label = f"{self.cd}hr" if mn == 0 else f"{hr}hr {mn}min"
                    else:
                        mn = int(math.ceil(remaining/60))
                        attack_label = f"{mn}min"
                    disabled = remaining > 0
                    style = ButtonStyle.danger if disabled else ButtonStyle.primary
                else:
                    attack_label = "Attacked"  # Changed from "Attack"
                    disabled = False
                    style = ButtonStyle.primary
                attack_btn = ui.Button(
                    label=attack_label,
                    style=style,
                    custom_id=attack_custom_id,
                    disabled=disabled,
                    row=r+1
                )
                if entry["last"]:
                    attack_btn.last_attack = entry["last"]
                attack_btn.callback = callback_func
                self.add_item(name_btn)
                self.add_item(attack_btn)

    # Updated callback to update only the pressed button and attach new last_attack timestamp
    def create_callback(self, member):
        async def callback(interaction):
            try:
                now = datetime.datetime.now(datetime.timezone.utc)
                await interaction.response.defer()
                
                await self.pool.execute(
                    """
                    INSERT INTO war_attacks(guild_id, member, last_attack)
                    VALUES($1,$2,NOW())
                    ON CONFLICT (guild_id, member)
                    DO UPDATE SET last_attack = NOW()
                    """,
                    self.guild_id, member
                )
                
                # Update cache and rebuild view to show cooldown
                for member_entry in self.members:
                    if member_entry["name"] == member:
                        member_entry["last"] = now
                        break
                self.rebuild_view()
                await interaction.edit_original_response(view=self)
            except Exception as e:
                try:
                    await interaction.followup.send(f"Error: {e}", ephemeral=True)
                except Exception:
                    pass
        return callback

    def create_colony_callback(self, ident):
        async def callback(interaction):
            try:
                now = datetime.datetime.now(datetime.timezone.utc)
                await interaction.response.defer()
                
                await self.pool.execute(
                    """
                    INSERT INTO war_attacks(guild_id, member, last_attack)
                    VALUES($1,$2,NOW())
                    ON CONFLICT (guild_id, member)
                    DO UPDATE SET last_attack = NOW()
                    """,
                    self.guild_id, ident
                )
                
                # Update cache and rebuild view to show cooldown
                for colony in self.colonies:
                    if colony["ident"] == ident:
                        colony["last"] = now
                        break
                self.rebuild_view()
                await interaction.edit_original_response(view=self)
            except Exception as e:
                try:
                    await interaction.followup.send(f"Error: {e}", ephemeral=True)
                except Exception:
                    pass
        return callback

    # New method to update all buttons live with their remaining cooldown
    async def start_countdown(self, message):
        import asyncio
        while not self.is_finished():
            updated = False
            now = datetime.datetime.now(datetime.timezone.utc)
            for item in self.children:
                # Skip buttons in the "Attacking..." state
                if hasattr(item, 'is_attacking') and item.is_attacking:
                    continue
                # Only update buttons with an active cooldown
                if hasattr(item, 'last_attack'):
                    elapsed = (now - item.last_attack).total_seconds()
                    # TEMPORARY: Convert 5 minutes to seconds (300) instead of hours
                    remaining = max(0, 300 - elapsed)
                    if remaining <= 0:
                        # Find the name/identity of what respawned
                        custom_id = item.custom_id
                        if custom_id.startswith("war_atk:"):
                            member_name = custom_id.replace("war_atk:", "")
                            await message.channel.send(f"✨ **{member_name}** has respawned!")
                        elif custom_id.startswith("war_col_atk:"):
                            colony_id = custom_id.replace("war_col_atk:colony:", "")
                            # Find colony info from cache
                            for colony in self.colonies:
                                if colony["ident"] == f"colony:{colony_id}":
                                    await message.channel.send(f"✨ Colony at **SB{colony['starbase']} ({colony['x']},{colony['y']})** has respawned!")
                                    break
                        
                        new_label = "Attacked"
                        item.style = ButtonStyle.primary
                        item.disabled = False
                        del item.last_attack
                    else:
                        if remaining >= 3600:
                            hr = int(remaining // 3600)
                            mn = int((remaining % 3600) // 60)
                            # If minutes equals 0, display as full cooldown.
                            new_label = f"{self.cd}hr" if mn == 0 else f"{hr}hr {mn}min"
                        else:
                            mn = int(math.ceil(remaining/60))
                            new_label = f"{mn}min"
                        item.style = ButtonStyle.danger
                        item.disabled = True
                    if new_label != item.label:
                        item.label = new_label
                        updated = True
            if updated:
                try:
                    await message.edit(view=self)
                except Exception as e:
                    print("Error updating view:", e)
            # Sleep until the next minute boundary to reduce update frequency.
            await asyncio.sleep(5)