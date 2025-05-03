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

    async def populate(self):
        try:
            # Cache members list for faster pagination.
            if not hasattr(self, "members"):
                war = await get_current_war(self.pool, self.guild_id)
                if not war:
                    raise ValueError("No active war found for this guild.")
                members_data = await self.pool.fetch(
                    "SELECT member, main_sb FROM members WHERE alliance=$1 ORDER BY main_sb DESC",
                    war["enemy_alliance"]
                )
                now = datetime.datetime.now(datetime.timezone.utc)
                self.members = []
                for rec in members_data:
                    m_name = rec["member"]
                    last = await self.pool.fetchval(
                        "SELECT last_attack FROM war_attacks WHERE guild_id=$1 AND member=$2",
                        self.guild_id, m_name
                    )
                    self.members.append({"name": m_name, "last": last})
                # Store maximum member name length for alignment.
                self.max_name_length = max((len(m["name"]) for m in self.members), default=0)
            now = datetime.datetime.now(datetime.timezone.utc)
            members = self.members

            # Set up pagination: 2 columns x 4 rows = 8 members per page
            members_per_column = 4
            columns = 2
            page_size = members_per_column * columns
            total_pages = math.ceil(len(members) / page_size) if members else 1
            self.current_page = getattr(self, "current_page", 0)
            # Ensure current_page is within bounds
            if self.current_page < 0:
                self.current_page = 0
            elif self.current_page >= total_pages:
                self.current_page = total_pages - 1

            # Clear existing items before repopulating
            self.clear_items()

            # Row 0: Pagination controls (Previous, Page tracker, Next, Refresh)
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
                    await self.populate()
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
                    await self.populate()
                    await interaction.edit_original_response(view=self)
                next_btn.callback = next_page
                items.append(next_btn)
            # Add the refresh button always
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

            for btn in items:
                btn.row = 0
                self.add_item(btn)

            # Rows 1 to members_per_column: build grid of member pairs
            for r in range(members_per_column):
                # Prepare buttons for one row (each row will have 2 member pairs if available)
                row_buttons = []
                for c in range(columns):
                    idx = self.current_page * page_size + (c * members_per_column + r)
                    if idx >= len(members):
                        continue
                    member = members[idx]
                    # Replace non-breaking spaces with FIGURE SPACE (U+2007) for padding.
                    padded_name = member["name"].ljust(self.max_name_length, "\u2007")
                    name_btn = ui.Button(
                        label=padded_name,
                        style=ButtonStyle.secondary,
                        custom_id=f"label:{member['name']}",
                        disabled=True,
                        row=r+1
                    )
                    # Compute attack button details based on cooldown
                    if member["last"]:
                        elapsed = (now - member["last"]).total_seconds()
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
                        attack_label = "Attack"
                        disabled = False
                        style = ButtonStyle.primary
                    # Create an attack button
                    attack_btn = ui.Button(
                        label=attack_label,
                        style=style,
                        custom_id=f"war_atk:{member['name']}",
                        disabled=disabled,
                        row=r+1
                    )
                    if member["last"]:
                        attack_btn.last_attack = member["last"]
                    attack_btn.callback = self.create_callback(member["name"])
                    # Append both buttons for this member (maintaining order: name then attack)
                    row_buttons.extend([name_btn, attack_btn])
                # Add all buttons in the current row (Discord groups by same row index)
                for btn in row_buttons:
                    self.add_item(btn)

        except Exception as e:
            print(f"Error populating WarView: {e}")

    # Updated callback to update only the pressed button and attach new last_attack timestamp
    def create_callback(self, member):
        """
        Factory function to create a unique callback for each button.
        Records a new attack timestamp for 'member' and updates the button state:
            - Inserts or updates war_attacks.last_attack to NOW().
            - Sets the button into its cooldown appearance.
            - Edits the original message to refresh the View.
        """
        async def callback(interaction):
            try:
                now = datetime.datetime.now(datetime.timezone.utc)
                # Upsert the last_attack time in war_attacks
                await self.pool.execute(
                    """
                    INSERT INTO war_attacks(guild_id, member, last_attack)
                    VALUES($1,$2,NOW())
                    ON CONFLICT (guild_id, member)
                    DO UPDATE SET last_attack = NOW()
                    """,
                    self.guild_id, member
                )
                # Update only the pressed button and store its last_attack for live countdown
                for item in self.children:
                    if item.custom_id == f"war_atk:{member}":
                        item.last_attack = now
                        # Immediately show full cooldown in "Xhr 0min" format.
                        item.label = f"{self.cd}hr"
                        item.style = ButtonStyle.danger
                        item.disabled = True
                        break
                await interaction.response.edit_message(view=self)
            except Exception as e:
                await interaction.response.send_message(f"Error: {e}", ephemeral=True)
        return callback

    # New method to update all buttons live with their remaining cooldown
    async def start_countdown(self, message):
        import asyncio
        while not self.is_finished():
            updated = False
            now = datetime.datetime.now(datetime.timezone.utc)
            for item in self.children:
                # Only update buttons with an active cooldown
                if hasattr(item, 'last_attack'):
                    elapsed = (now - item.last_attack).total_seconds()
                    remaining = max(0, self.cd * 3600 - elapsed)
                    if remaining <= 0:
                        new_label = "Attack"
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