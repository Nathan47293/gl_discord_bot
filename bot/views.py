# bot/views.py
import datetime, math
from discord import ButtonStyle, ui

from .db import get_current_war

class WarView(ui.View):
    def __init__(self, guild_id: str, cooldown_hours: int, pool):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.cd = cooldown_hours
        self.pool = pool

    async def populate(self):
        war = await get_current_war(self.pool, self.guild_id)
        members = await self.pool.fetch(
            "SELECT member, main_sb FROM members WHERE alliance=$1 ORDER BY main_sb DESC",
            war["enemy_alliance"]
        )
        now = datetime.datetime.utcnow()
        for rec in members:
            last = await self.pool.fetchval(
                "SELECT last_attack FROM war_attacks WHERE guild_id=$1 AND member=$2",
                self.guild_id, rec["member"]
            )
            if last:
                delta = (now - last).total_seconds()
                remaining = max(0, self.cd * 3600 - delta)
            else:
                remaining = 0

            if remaining > 0:
                label, style, disabled = f"{math.ceil(remaining/3600)}h", ButtonStyle.danger, True
            else:
                label, style, disabled = "Attacked", ButtonStyle.primary, False

            btn = ui.Button(
                label=label,
                style=style,
                custom_id=f"war_atk:{rec['member']}",
                disabled=disabled
            )

            async def callback(inter, button=btn, member=rec["member"]):
                await self.pool.execute(
                    """INSERT INTO war_attacks(guild_id, member, last_attack)
                       VALUES($1,$2,NOW())
                       ON CONFLICT (guild_id, member) DO UPDATE SET last_attack=NOW()""",
                    self.guild_id, member
                )
                button.label = f"{self.cd}h"
                button.style = ButtonStyle.danger
                button.disabled = True
                await inter.response.edit_message(view=self)

            btn.callback = callback
            self.add_item(btn)
