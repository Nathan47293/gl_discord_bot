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
    def __init__(self, guild_id: str, cooldown_hours: int, pool, bot=None):
        """
        :param guild_id: Discord guild (server) ID as string
        :param cooldown_hours: The number of hours of cooldown after an attack
        :param pool: asyncpg Pool instance for DB operations
        :param bot: Reference to the bot instance for task creation
        """
        # Initialize a persistent View (timeout=None means it never times out)
        super().__init__(timeout=None)
        self.guild_id = guild_id      # Store guild context
        self.cd = cooldown_hours      # Store cooldown duration
        self.pool = pool              # DB pool for fetching records
        self.bot = bot                # Store bot reference for task creation
        self._countdown_task = None
        self._last_update = None  # Track last visual update
        self.current_page = 0
        self.mode = "main"      # "main" for members, "colony" for colonies
        self.colonies = []      # cache for colony mode
        self.members = []       # cache for main mode
        self.notified_respawns = dict()  # Store as dict with timestamps
        self.notified_keys = set()  # Track just the keys for fast lookup
        self.notification_timestamps = {}  # Store timestamps separately
        self.last_timer_update = datetime.datetime.now(datetime.timezone.utc)
        self.update_interval = 300  # 5 minutes in seconds
        self.message = None
        self.channel = None
        self.channel_id = None
        self.message_id = None
        self.expired_records = []
        self.error_count = 0
        self.respawn_notifications = {}  # Just use a single dict for tracking

    async def send_safe(self, channel, message):
        """Helper method to safely send messages with permission checking"""
        try:
            # Check if bot has required permissions
            permissions = channel.permissions_for(channel.guild.me)
            if not permissions.send_messages:
                print(f"Missing send_messages permission in channel {channel.id}")
                return False
                
            await channel.send(message)
            return True
        except discord.Forbidden:
            print(f"Missing permissions to send messages in channel {channel.id}")
            return False
        except Exception as e:
            print(f"Error sending message: {e}")
            return False

    async def populate(self):
        try:
            print("\n=== Starting populate() ===")
            print(f"View ID: {id(self)}")
            print(f"Has parent_cog: {hasattr(self, 'parent_cog')}")
            print(f"Has channel: {hasattr(self, 'channel')}")

            # Clean up old war attacks that aren't associated with active wars
            try:
                result = await self.pool.execute("""
                    DELETE FROM war_attacks 
                    WHERE guild_id NOT IN (SELECT guild_id FROM wars)
                """)
                print(f"Cleaned up war attacks from inactive wars: {result}")
            except Exception as e:
                print(f"Error cleaning up old war attacks: {e}")
            
            # Continue with normal population...
            # Store channel reference for messages
            self.message_channel = getattr(self, 'message', None)
            if self.message_channel:
                self.message_channel = self.message_channel.channel

            # Preload both main and colony data regardless of current mode
            war = await get_current_war(self.pool, self.guild_id)
            if war:
                enemy = war["enemy_alliance"]
            elif hasattr(self, "enemy_alliance"):
                enemy = self.enemy_alliance
            else:
                raise ValueError("No active war found for this guild.")

            # Load main members
            members_data = await self.pool.fetch(
                "SELECT member, main_sb FROM members WHERE alliance=$1 ORDER BY main_sb DESC",
                enemy
            )
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

            # Load colonies
            colonies_data = await self.pool.fetch(
                "SELECT id, starbase, x, y FROM colonies WHERE alliance=$1 ORDER BY starbase DESC, x, y",
                enemy
            )
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

            # Update view for current mode
            await self.rebuild_view()

        except Exception as e:
            print(f"Error populating WarView: {e}")

    async def rebuild_view(self):
        """Only rebuild if references are valid"""
        try:
            # Only validate references if countdown has started
            if self._countdown_task:
                # Check task status safely
                try:
                    if not self._countdown_task.done() and not self._countdown_task.cancelled():
                        if not await self.refresh_references():
                            print("Cannot rebuild - invalid references")
                            return False
                except Exception as e:
                    print(f"Error checking task status: {e}")
            
            # For initial build or task error, continue with build
            now = datetime.datetime.now(datetime.timezone.utc)
            cache = self.members if self.mode=="main" else self.colonies

            # Clear expired attacks from cache and log
            expired_count = 0
            for entry in cache:
                if entry["last"]:
                    elapsed = (now - entry["last"]).total_seconds()
                    if elapsed >= self.cd * 3600:
                        entry["last"] = None
                        expired_count += 1
            
            if expired_count > 0:
                print(f"Cleared {expired_count} expired attacks from cache")

            # Rebuild pagination and grid from cached self.members without DB queries.
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
                    await self.rebuild_view()  # Add await here
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
                    await self.rebuild_view()  # Add await here
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
                    await self.rebuild_view()  # Colony data already loaded
                    await interaction.edit_original_response(view=self)
                mode_btn.callback = switch_to_colony
            else:
                mode_btn = ui.Button(label="Main Planets", style=ButtonStyle.primary, custom_id="mode:main")
                async def switch_to_main(interaction):
                    await interaction.response.defer()
                    self.mode = "main"
                    self.current_page = 0
                    await self.rebuild_view()  # Main data already loaded
                    await interaction.edit_original_response(view=self)
                mode_btn.callback = switch_to_main
            items.append(mode_btn)

            for btn in items:
                btn.row = 0
                self.add_item(btn)

            # Build member grid:
            for r in range(members_per_column):
                for c in range(columns):
                    idx = self.current_page * page_size + (c * members_per_column + r)
                    if idx >= len(cache):
                        continue
                    entry = cache[idx]
                    
                    # Create name button
                    if self.mode == "main":
                        label = f"{entry['name']} SB{entry['main_sb']}"
                        custom_id_prefix = "war_atk:"
                        callback_func = self.create_callback(entry["name"])
                    else:
                        label = f"SB{entry['starbase']} ({entry['x']},{entry['y']})"
                        custom_id_prefix = "war_col_atk:"
                        callback_func = self.create_colony_callback(entry["ident"])

                    name_btn = ui.Button(
                        label=label,
                        style=ButtonStyle.secondary,
                        custom_id=f"label:{idx}",
                        disabled=True,
                        row=r+1
                    )
                    
                    # Create attack button with all properties set
                    attack_custom_id = f"{custom_id_prefix}{entry['name'] if self.mode == 'main' else entry['ident']}"
                    
                    attack_btn = ui.Button(
                        custom_id=attack_custom_id,
                        row=r+1
                    )
                    
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
                        
                        style = ButtonStyle.danger
                        disabled = False  # Allow clicking cooldown buttons
                        
                        # Only set expiry if remaining time is positive
                        if remaining > 0:  # Add check here
                            attack_btn.expiry = entry["last"] + datetime.timedelta(hours=self.cd)
                            # Store the member/colony info for countdown messages
                            if self.mode == "main":
                                attack_btn.member_name = entry["name"]
                            else:
                                attack_btn.colony_info = {
                                    "starbase": entry["starbase"],
                                    "x": entry["x"],
                                    "y": entry["y"]
                                }
                    else:
                        attack_label = "Attacked"
                        disabled = False
                        style = ButtonStyle.primary

                    # Update button properties after creation
                    attack_btn.label = attack_label
                    attack_btn.style = style
                    attack_btn.disabled = disabled
                    attack_btn.callback = callback_func
                    
                    if entry["last"]:
                        attack_btn.last_attack = entry["last"]

                    self.add_item(name_btn)
                    self.add_item(attack_btn)

        except Exception as e:
            print(f"Error in rebuild_view: {e}")
            return False

        return True

    # Updated callback to update only the pressed button and attach new last_attack timestamp
    def create_callback(self, member):
        async def callback(interaction):
            try:
                now = datetime.datetime.now(datetime.timezone.utc)
                await interaction.response.defer()

                # Find and update the member entry
                for member_entry in self.members:
                    if member_entry["name"] == member:
                        if member_entry["last"] is not None:
                            member_entry["last"] = None
                            # Update visuals first
                            await self.rebuild_view()
                            await interaction.edit_original_response(view=self)
                            # Then delete from DB
                            await self.pool.execute(
                                "DELETE FROM war_attacks WHERE guild_id=$1 AND member=$2",
                                self.guild_id, member
                            )
                        else:
                            # Set cooldown first
                            member_entry["last"] = now
                            # Update visuals immediately
                            await self.rebuild_view()
                            await interaction.edit_original_response(view=self)
                            # Then update DB
                            await self.pool.execute(
                                """
                                INSERT INTO war_attacks(guild_id, member, last_attack)
                                VALUES($1,$2,NOW())
                                ON CONFLICT (guild_id, member)
                                DO UPDATE SET last_attack = NOW()
                                """,
                                self.guild_id, member
                            )
                        break

                # Update other views
                if hasattr(self, 'parent_cog'):
                    for view in self.parent_cog.active_views.values():
                        if view != self and view.message:
                            await view.rebuild_view()
                            await view.message.edit(view=view)

            except Exception as e:
                print(f"Error in button callback: {e}")
                try:
                    await interaction.followup.send("❌ Error processing button click", ephemeral=True)
                except:
                    pass
        return callback

    def create_colony_callback(self, ident):
        async def callback(interaction):
            try:
                now = datetime.datetime.now(datetime.timezone.utc)
                await interaction.response.defer()

                # Find and update the colony entry
                for colony in self.colonies:
                    if colony["ident"] == ident:
                        if colony["last"] is not None:
                            colony["last"] = None
                            # Update visuals first
                            await self.rebuild_view()
                            await interaction.edit_original_response(view=self)
                            # Then delete from DB
                            await self.pool.execute(
                                "DELETE FROM war_attacks WHERE guild_id=$1 AND member=$2",
                                self.guild_id, ident
                            )
                        else:
                            # Set cooldown first
                            colony["last"] = now
                            # Update visuals immediately
                            await self.rebuild_view()
                            await interaction.edit_original_response(view=self)
                            # Then update DB
                            await self.pool.execute(
                                """
                                INSERT INTO war_attacks(guild_id, member, last_attack)
                                VALUES($1,$2,NOW())
                                ON CONFLICT (guild_id, member)
                                DO UPDATE SET last_attack = NOW()
                                """,
                                self.guild_id, ident
                            )
                        break

                # Update other views
                if hasattr(self, 'parent_cog'):
                    for view in self.parent_cog.active_views.values():
                        if view != self and view.message:
                            await view.rebuild_view()
                            await view.message.edit(view=view)

            except Exception as e:
                print(f"Error in colony callback: {e}")
                try:
                    await interaction.followup.send("❌ Error processing button click", ephemeral=True)
                except:
                    pass
        return callback

    async def start_countdown(self, message):
        """Initialize countdown with proper reference storage"""
        import asyncio
        
        # Store references before starting countdown
        self.message = message
        self.channel = message.channel
        self.message_id = message.id
        self.channel_id = message.channel.id

        # Reset notification tracking
        self.respawn_notifications.clear()
        
        try:
            await self.channel.send("✨ War tracker initialized - I will notify when targets respawn!")
        except:
            print("Failed to send initialization message")
        
        expired_records = []  # Track expired records
        error_count = 0  # Track consecutive errors
        
        while not self.is_finished():
            try:
                now = datetime.datetime.now(datetime.timezone.utc)
                
                # Clean up old notifications - simplified
                expired = {
                    k for k, t in self.respawn_notifications.items()
                    if (now - t).total_seconds() >= 3600  # 1 hour
                }
                for k in expired:
                    self.respawn_notifications.pop(k)

                # Refresh references after potential session resume
                if not await self.refresh_references():
                    self.error_count += 1
                    if self.error_count >= 3:
                        print("Lost critical references, stopping countdown")
                        return
                    await asyncio.sleep(5)
                    continue

                now = datetime.datetime.now(datetime.timezone.utc)
                updated = False
                force_update = False  # Flag for forced updates on cooldown expiry
                expired_records_set = set()  # Track expired records with a set

                # Only process timer updates every 5 minutes
                time_since_update = (now - self.last_timer_update).total_seconds()
                should_update_timers = time_since_update >= self.update_interval

                # Clean up old notification keys first
                try:
                    valid_keys = set()
                    for key in self.notified_respawns:
                        try:
                            parts = key.split(':')
                            if len(parts) != 3:
                                continue
                            timestamp = datetime.datetime.fromisoformat(parts[2])
                            if (now - timestamp) < datetime.timedelta(hours=1):
                                valid_keys.add(key)
                        except (ValueError, IndexError):
                            continue
                    self.notified_respawns = valid_keys
                except Exception as e:
                    print(f"Error cleaning notification keys: {e}")
                    self.notified_respawns = set()  # Reset if error

                # Handle timer updates
                for item in self.children:
                    if not hasattr(item, 'expiry') or item.expiry is None:
                        continue
                        
                    try:
                        time_left = (item.expiry - now).total_seconds()
                    except TypeError:
                        continue

                    if time_left <= 0:
                        item.label = "Attacked"
                        item.style = ButtonStyle.primary
                        item.disabled = False
                        delattr(item, 'expiry')
                        updated = True
                        force_update = True  # Force update when cooldown expires
                    elif should_update_timers:
                        # Only update labels every 5 minutes
                        if time_left >= 3600:
                            hr = int(time_left // 3600)
                            mn = int((time_left % 3600) // 60)
                            new_label = f"{self.cd}hr" if mn == 0 else f"{hr}hr {mn}min"
                        else:
                            mn = int(math.ceil(time_left/60))
                            new_label = f"{mn}min"
                        
                        if item.label != new_label:
                            item.label = new_label
                            updated = True

                # Only update last_timer_update if we actually processed updates
                if should_update_timers and (updated or force_update):
                    self.last_timer_update = now

                # Check for respawns and DB cleanup
                for member in self.members:
                    if member["last"] and (now - member["last"]).total_seconds() >= self.cd * 3600:
                        notify_key = f"member:{member['name']}"
                        if notify_key not in self.respawn_notifications:
                            await self.channel.send(f"✨ **{member['name']}** has respawned!")
                            self.respawn_notifications[notify_key] = now
                            expired_records_set.add(member["name"])
                        member["last"] = None
                        updated = True

                for colony in self.colonies:
                    if colony["last"] and (now - colony["last"]).total_seconds() >= self.cd * 3600:
                        notify_key = f"colony:{colony['ident']}"
                        if notify_key not in self.respawn_notifications:
                            await self.channel.send(f"✨ Colony at **SB{colony['starbase']} ({colony['x']},{colony['y']})** has respawned!")
                            self.respawn_notifications[notify_key] = now
                            expired_records_set.add(colony["ident"])
                        colony["last"] = None
                        updated = True

                # Fix timezone issue in notification cleanup
                self.notified_respawns = {
                    key for key in self.notified_respawns 
                    if (now - datetime.datetime.fromisoformat(key.split(':')[2]).replace(tzinfo=datetime.timezone.utc)) < datetime.timedelta(hours=1)
                }

                # Delete expired records if we have any
                if expired_records_set:
                    try:
                        # Convert set to list for DB operation
                        expired_records = list(expired_records_set)
                        print(f"Deleting expired war attacks: {expired_records}")
                        result = await self.pool.execute(
                            "DELETE FROM war_attacks WHERE guild_id=$1 AND member=ANY($2)",
                            self.guild_id, expired_records
                        )
                        print(f"Delete result: {result}")
                    except Exception as e:
                        print(f"Error deleting expired records: {e}")

                # Cleanup old notification keys (over 1 hour old)
                self.notified_respawns = {
                    key for key in self.notified_respawns 
                    if now - datetime.datetime.fromisoformat(key.split(':')[2]) < datetime.timedelta(hours=1)
                }

                # Batch delete expired records
                if expired_records:
                    try:
                        await self.pool.execute(
                            "DELETE FROM war_attacks WHERE guild_id=$1 AND member=ANY($2)",
                            self.guild_id, expired_records
                        )
                        expired_records = []  # Clear after successful deletion
                    except Exception as e:
                        print(f"Error deleting expired records: {e}")

                # Update view if needed
                if updated or force_update:
                    try:
                        # Check if message still exists and is accessible
                        try:
                            # Fetch fresh message object
                            message = await self.channel.fetch_message(message.id)
                            self.message = message  # Update reference
                            await message.edit(view=self)
                            error_count = 0  # Reset error counter on success
                        except discord.NotFound:
                            print("War message was deleted, stopping countdown")
                            return
                        except discord.Forbidden:
                            print("Lost permissions to edit message")
                            return
                            
                        # Update other views with error handling
                        if hasattr(self, 'parent_cog'):
                            for guild_id, view in self.parent_cog.active_views.items():
                                if view != self and view.message:
                                    try:
                                        # Fetch fresh message for other views too
                                        other_msg = await view.channel.fetch_message(view.message.id)
                                        view.message = other_msg
                                        await view.rebuild_view()
                                        await other_msg.edit(view=view)
                                    except (discord.NotFound, discord.Forbidden):
                                        # Remove invalid views
                                        self.parent_cog.active_views.pop(guild_id, None)
                                    except Exception as e:
                                        print(f"Error updating other view: {e}")

                    except Exception as e:
                        error_count += 1
                        print(f"Error updating view: {e}")
                        
                        # If too many consecutive errors, stop the countdown
                        if error_count >= 5:
                            print("Too many consecutive errors, stopping countdown")
                            return

            except Exception as e:
                print(f"Error in countdown loop: {e}")
                if hasattr(e, '__traceback__'):
                    import traceback
                    traceback.print_tb(e.__traceback__)
            
            await asyncio.sleep(30)  # Check every 30 seconds, but only update UI every 5 minutes

    async def refresh_references(self):
        """Refresh channel and message references after session resume"""
        try:
            if not self.bot or not self.channel_id:
                return False

            # Get fresh channel reference
            channel = self.bot.get_channel(self.channel_id)
            if not channel:
                try:
                    channel = await self.bot.fetch_channel(self.channel_id)
                except:
                    return False
            
            self.channel = channel
            
            # Get fresh message reference if we have an ID
            if self.message_id:
                try:
                    self.message = await channel.fetch_message(self.message_id)
                except:
                    return False

            return True
        except Exception as e:
            print(f"Error refreshing references: {e}")
            return False

    # Fix mode switch callbacks
    async def switch_to_colony(self, interaction):
        await interaction.response.defer()
        self.mode = "colony"
        self.current_page = 0
        await self.rebuild_view()  # Colony data already loaded
        await interaction.edit_original_response(view=self)

    async def switch_to_main(self, interaction):
        await interaction.response.defer()
        self.mode = "main"
        self.current_page = 0
        await self.rebuild_view()  # Main data already loaded
        await interaction.edit_original_response(view=self)