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
        """
        Dynamically add a Button for each enemy member, ordered by descending main SB.
        - Queries the 'wars' table to get the current enemy alliance.
        - Fetches all members in that alliance sorted by main starbase level (descending).
        - For each member:
            * Checks the war_attacks table for their last_attack timestamp.
            * Computes remaining seconds of cooldown: cd * 3600 - elapsed.
            * If still on cooldown, the button shows remaining hours and is disabled (red).
            * Otherwise, the button reads "Attacked", is enabled (purple), and clicking it
              will record a new attack timestamp.
        """
        try:
            # Retrieve the current war row
            war = await get_current_war(self.pool, self.guild_id)
            if not war:
                raise ValueError("No active war found for this guild.")

            # Fetch all members from the enemy alliance
            members = await self.pool.fetch(
                "SELECT member, main_sb FROM members"
                " WHERE alliance=$1 ORDER BY main_sb DESC",
                war["enemy_alliance"]
            )
            # Use offset-aware datetime
            now = datetime.datetime.now(datetime.timezone.utc)

            for rec in members:
                member_name = rec["member"]
                # Fetch individual enemy's last_attack timestamp
                last = await self.pool.fetchval(
                    "SELECT last_attack FROM war_attacks WHERE guild_id=$1 AND member=$2",
                    self.guild_id, member_name
                )

                if last:
                    remaining = max(0, self.cd * 3600 - (now - last).total_seconds())
                    if remaining >= 3600:
                        hr = int(remaining // 3600)
                        mn = int((remaining % 3600) // 60)
                        # If minutes equals 0, display as full cooldown
                        label = f"{self.cd}hr" if mn == 0 else f"{hr}hr {mn}min"
                    else:
                        mn = int(math.ceil(remaining/60))
                        label = f"{mn}min"
                else:
                    remaining = 0
                    label = "Attack"
                style = ButtonStyle.danger if remaining > 0 else ButtonStyle.primary
                disabled = remaining > 0

                # Create the button
                btn = ui.Button(
                    label=label,
                    style=style,
                    custom_id=f"war_atk:{member_name}",
                    disabled=disabled
                )

                # If on cooldown, attach the last_attack timestamp for live updates
                if last:
                    btn.last_attack = last

                # Attach a unique callback
                btn.callback = self.create_callback(member_name)
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