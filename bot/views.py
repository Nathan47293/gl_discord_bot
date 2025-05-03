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
        # 1) Retrieve the current war row, which includes 'enemy_alliance'
        war = await get_current_war(self.pool, self.guild_id)
        # 2) Fetch all members from that enemy alliance, sorted by main starbase desc
        members = await self.pool.fetch(
            "SELECT member, main_sb FROM members"
            " WHERE alliance=$1 ORDER BY main_sb DESC",
            war["enemy_alliance"]
        )
        now = datetime.datetime.utcnow()  # Current UTC time for cooldown calc

        # 3) Loop over each member record
        for rec in members:
            member_name = rec["member"]
            # 3a) Get last attack timestamp for this member (if any)
            last = await self.pool.fetchval(
                "SELECT last_attack FROM war_attacks"
                " WHERE guild_id=$1 AND member=$2",
                self.guild_id, member_name
            )

            # 3b) Compute how many seconds remain in the cooldown
            if last:
                elapsed = (now - last).total_seconds()
                remaining = max(0, self.cd * 3600 - elapsed)
            else:
                remaining = 0  # Never attacked yet

            # 3c) Determine button label, style, and disabled state
            if remaining > 0:
                # If still on cooldown, show hours left, red danger style
                hours_left = math.ceil(remaining / 3600)
                label = f"{hours_left}h"
                style = ButtonStyle.danger
                disabled = True
            else:
                # Otherwise, allow a new attack: "Attacked" label, purple primary style
                label = "Attacked"
                style = ButtonStyle.primary
                disabled = False

            # 4) Create the Button with a unique custom_id to identify which member
            btn = ui.Button(
                label=label,
                style=style,
                custom_id=f"war_atk:{member_name}",
                disabled=disabled
            )

            # 5) Define the callback for when this button is clicked
            async def callback(interaction, button=btn, member=member_name):
                """
                Records a new attack timestamp for 'member' and updates the button state:
                - Inserts or updates the war_attacks.last_attack to NOW().
                - Sets the button into its cooldown appearance.
                - Edits the original message to refresh the View.
                """
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
                # Update the button in-place
                button.label = f"{self.cd}h"
                button.style = ButtonStyle.danger
                button.disabled = True
                # Edit the message to re-render the buttons with new state
                await interaction.response.edit_message(view=self)

            # 6) Attach the callback and add the button to this View
            btn.callback = callback
            self.add_item(btn)