import os
import random
import asyncio
import json
from datetime import datetime, timezone, timedelta
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Modal, TextInput

try:
    from keep_alive import keep_alive
except ImportError:
    def keep_alive():
        pass


OWNER_ID = 1255544682759323680

ban_limit = 5
channel_limit = 3
spam_limit = 5
custom_prefix = ","

ticket_log_channel_id = None
custom_ticket_ping = "{role}"
ticket_configs = {}

invite_log_channel_id = None
welcome_channel_id = None
custom_welcome_msg = None
custom_welcome_img = None
welcome_enabled = False

invites_cache = {}
spam_tracking = {}
nuke_tracking = {}

CONFIG_FILE = "bot_settings.json"

def save_persistent_settings():
    data = {
        "ban_limit": ban_limit,
        "channel_limit": channel_limit,
        "spam_limit": spam_limit,
        "custom_prefix": custom_prefix,
        "ticket_log_channel_id": ticket_log_channel_id,
        "custom_ticket_ping": custom_ticket_ping,
        "ticket_configs": ticket_configs,
        "invite_log_channel_id": invite_log_channel_id,
        "welcome_channel_id": welcome_channel_id,
        "custom_welcome_msg": custom_welcome_msg,
        "custom_welcome_img": custom_welcome_img,
        "welcome_enabled": welcome_enabled
    }
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[CONFIG SAVE ERROR] {e}")

def load_persistent_settings():
    global ban_limit, channel_limit, spam_limit, custom_prefix
    global ticket_log_channel_id, custom_ticket_ping, ticket_configs
    global invite_log_channel_id, welcome_channel_id
    global custom_welcome_msg, custom_welcome_img, welcome_enabled
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        ban_limit = data.get("ban_limit", ban_limit)
        channel_limit = data.get("channel_limit", channel_limit)
        spam_limit = data.get("spam_limit", spam_limit)
        custom_prefix = data.get("custom_prefix", custom_prefix)
        ticket_log_channel_id = data.get("ticket_log_channel_id")
        custom_ticket_ping = data.get("custom_ticket_ping", custom_ticket_ping)
        ticket_configs = {int(k): v for k, v in data.get("ticket_configs", {}).items()}
        invite_log_channel_id = data.get("invite_log_channel_id")
        welcome_channel_id = data.get("welcome_channel_id")
        custom_welcome_msg = data.get("custom_welcome_msg")
        custom_welcome_img = data.get("custom_welcome_img")
        welcome_enabled = data.get("welcome_enabled", False)
        print("[CONFIG] Persistent settings loaded.")
    except FileNotFoundError:
        print("[CONFIG] No saved settings found; using defaults.")
    except Exception as e:
        print(f"[CONFIG LOAD ERROR] {e}")


intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.invites = True
intents.message_content = True


_persistent_views_loaded = False

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or(custom_prefix),
    intents=intents,
    help_command=None
)


def is_whitelisted(user: discord.Member, guild: discord.Guild) -> bool:
    return bool(OWNER_ID and user.id == OWNER_ID) or user.guild_permissions.administrator


def parse_time(duration_str: str) -> int:
    multipliers = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400
    }

    if not duration_str:
        return 60

    unit = duration_str[-1].lower()
    val = duration_str[:-1]

    return (
        int(val) * multipliers[unit]
        if unit in multipliers and val.isdigit()
        else 60
    )


def _has_perm(member: discord.Member, permission: str) -> bool:
    return bool(getattr(member.guild_permissions, permission, False))


def _hierarchy_error(ctx, target: discord.Member) -> str | None:
    guild = ctx.guild
    author = ctx.author
    bot_member = guild.me

    if target == author:
        return "❌ You cannot use this command on yourself."

    if target == guild.owner:
        return "❌ You cannot use this command on the server owner."

    if bot_member and target.top_role >= bot_member.top_role:
        return "❌ I cannot act on this member because their highest role is equal to or higher than mine."

    if author.id != OWNER_ID and target.top_role >= author.top_role:
        return "❌ You cannot act on a member with an equal or higher role than yours."

    return None


def permission_check(permission: str):
    async def check(ctx: commands.Context):
        return ctx.author.id == OWNER_ID or _has_perm(ctx.author, permission)

    return commands.check(check)


def app_permission_check(permission: str):
    async def check(interaction: discord.Interaction):
        user = interaction.user

        return (
            user.id == OWNER_ID
            or (
                isinstance(user, discord.Member)
                and _has_perm(user, permission)
            )
        )

    return app_commands.check(check)


def owner_or_permission(ctx, permission: str) -> bool:
    return ctx.author.id == OWNER_ID or _has_perm(ctx.author, permission)


# =========================================================
# READY
# =========================================================

@bot.event
async def on_ready():
    load_persistent_settings()
    print("==================================================")
    print(f" Logged in successfully as {bot.user}")
    print(f" Bot ID: {bot.user.id}")
    print("==================================================")

    try:
        synced = await bot.tree.sync()
        print(f" [Slash Commands] Successfully synced {len(synced)} command(s).")
    except Exception as e:
        print(f" [Slash Commands] Failed to sync commands: {e}")

    global _persistent_views_loaded
    if not _persistent_views_loaded:
        try:
            bot.add_view(TicketControlView())
            migrated = False
            for guild_id in list(ticket_configs):
                before = "panels" in ticket_configs[guild_id]
                data = ensure_ticket_structure(guild_id)
                if data and not before:
                    migrated = True
                if data:
                    for panel_id, panel in data.get("panels", {}).items():
                        if panel.get("buttons"):
                            bot.add_view(DynamicTicketButtonView(panel_id, panel["buttons"]))
            if migrated:
                save_persistent_settings()
            _persistent_views_loaded = True
        except Exception as e:
            print(f"[PERSISTENT VIEW ERROR] {e}")

    for guild in bot.guilds:
        try:
            invites_cache[guild.id] = await guild.invites()
            print(f" [Invite Cache] Cached invites for guild: {guild.name}")
        except Exception as e:
            print(f" [Invite Cache] Could not cache invites for {guild.name}: {e}")

    print("==================================================")


# =========================================================
# MESSAGE / ANTI-SPAM
# =========================================================

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    user_id = message.author.id
    guild = message.guild

    if guild and not is_whitelisted(message.author, guild):
        current_time = datetime.now().timestamp()

        if user_id not in spam_tracking:
            spam_tracking[user_id] = []

        spam_tracking[user_id] = [
            t for t in spam_tracking[user_id]
            if current_time - t < 5
        ]

        spam_tracking[user_id].append(current_time)

        if len(spam_tracking[user_id]) >= spam_limit:
            try:
                duration = timedelta(minutes=5)

                await message.author.timeout(
                    duration,
                    reason="Anti-Spam: Sending messages too quickly."
                )

                spam_tracking[user_id] = []

                warning_msg = await message.channel.send(
                    f"⚠️ {message.author.mention} has been **timed out for 5 minutes** for spamming."
                )

                await asyncio.sleep(6)
                await warning_msg.delete()

            except Exception:
                pass

    if message.content.startswith(custom_prefix):
        ctx = await bot.get_context(message)

        if ctx.command:
            await bot.invoke(ctx)
            return

    await bot.process_commands(message)


# =========================================================
# ANTI-NUKE
# =========================================================

@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    try:
        async for entry in guild.audit_logs(
            limit=1,
            action=discord.AuditLogAction.ban
        ):
            executor = entry.user

            if executor.bot or is_whitelisted(executor, guild):
                return

            current_time = datetime.now().timestamp()

            if executor.id not in nuke_tracking:
                nuke_tracking[executor.id] = {
                    "bans": [],
                    "channels": []
                }

            nuke_tracking[executor.id]["bans"] = [
                t for t in nuke_tracking[executor.id]["bans"]
                if current_time - t < 120
            ]

            nuke_tracking[executor.id]["bans"].append(current_time)

            if len(nuke_tracking[executor.id]["bans"]) >= ban_limit:
                await guild.ban(
                    executor,
                    reason="Anti-Nuke Triggered: Mass banning members."
                )

                alert_channel = guild.system_channel

                if alert_channel:
                    await alert_channel.send(
                        f"🚨 **ANTI-NUKE ACTIVATED** 🚨\n"
                        f"User {executor.mention} was automatically banned for mass banning members."
                    )

            break

    except Exception as e:
        print(f"Error in on_member_ban anti-nuke: {e}")


@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    guild = channel.guild

    try:
        async for entry in guild.audit_logs(
            limit=1,
            action=discord.AuditLogAction.channel_delete
        ):
            executor = entry.user

            if executor.bot or is_whitelisted(executor, guild):
                return

            current_time = datetime.now().timestamp()

            if executor.id not in nuke_tracking:
                nuke_tracking[executor.id] = {
                    "bans": [],
                    "channels": []
                }

            nuke_tracking[executor.id]["channels"] = [
                t for t in nuke_tracking[executor.id]["channels"]
                if current_time - t < 120
            ]

            nuke_tracking[executor.id]["channels"].append(current_time)

            if len(nuke_tracking[executor.id]["channels"]) >= channel_limit:
                await guild.ban(
                    executor,
                    reason="Anti-Nuke Triggered: Mass deleting channels."
                )

                alert_channel = guild.system_channel

                if alert_channel:
                    await alert_channel.send(
                        f"🚨 **ANTI-NUKE ACTIVATED** 🚨\n"
                        f"User {executor.mention} was automatically banned for mass deleting channels."
                    )

            break

    except Exception as e:
        print(f"Error in on_guild_channel_delete anti-nuke: {e}")


# =========================================================
# TICKET SYSTEM
# =========================================================

def format_ticket_text(text, user, role=None):
    if not text:
        return None
    return (text
            .replace("{user}", user.mention)
            .replace("{username}", user.display_name)
            .replace("{role}", role.mention if role else "@here")
            .replace("{user.mention}", user.mention))


def new_ticket_panel(category, role, title="Support Hub", desc="Click a button below to open a support ticket."):
    cid = f"custom_ticket_{random.randint(100000, 999999)}"
    return {
        "category_id": category.id if category else None,
        "role_id": role.id if role else None,
        "title": title,
        "desc": desc,
        "ticket_title": "🎫 {type} Ticket",
        "ticket_desc": "Ticket opened by {user}\nPlease wait patiently.",
        "ticket_open_message": "{role} New ticket opened by {user}",
        "ticket_footer": "Opened by {user}",
        "ticket_color": 3447003,
        "buttons": {cid: {"label": "Support", "questions": []}}
    }


def ensure_ticket_structure(guild_id):
    data = ticket_configs.get(guild_id)
    if not data:
        return None
    # Migrate the old single-panel format automatically.
    if "panels" not in data:
        panel_id = f"panel_{random.randint(100000, 999999)}"
        old = dict(data)
        ticket_configs[guild_id] = {
            "active_panel": panel_id,
            "panels": {panel_id: old}
        }
        data = ticket_configs[guild_id]
    return data


def get_panel(guild_id, panel_id=None):
    data = ensure_ticket_structure(guild_id)
    if not data:
        return None
    panel_id = panel_id or data.get("active_panel")
    return data.get("panels", {}).get(panel_id)


def build_ticket_embed(config, button_label, user, answers=None):
    embed = discord.Embed(
        title=config.get("ticket_title", "🎫 {type} Ticket").replace("{type}", button_label).replace("{user}", user.mention),
        description=config.get("ticket_desc", "Ticket opened by {user}\nPlease wait patiently.").replace("{type}", button_label).replace("{user}", user.mention).replace("{username}", user.display_name),
        color=int(config.get("ticket_color", 3447003)),
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="Opened By", value=user.mention, inline=False)
    embed.add_field(name="Ticket Type", value=button_label, inline=False)
    for q, answer in answers or []:
        embed.add_field(name=q[:256], value=(answer[:1024] if answer else "Not Provided"), inline=False)
    footer = config.get("ticket_footer")
    if footer:
        embed.set_footer(text=footer.replace("{user}", user.display_name).replace("{username}", user.display_name).replace("{type}", button_label))
    return embed


async def create_ticket(interaction, panel_id, button_id, button_label, questions, answers=None):
    guild = interaction.guild
    user = interaction.user
    data = ensure_ticket_structure(guild.id) if guild else None
    panel = get_panel(guild.id, panel_id) if guild else None
    if not guild or not panel:
        return await interaction.followup.send("❌ Ticket panel is no longer available.", ephemeral=True)

    category = guild.get_channel(panel.get("category_id"))
    role = guild.get_role(panel.get("role_id")) if panel.get("role_id") else None
    if panel.get("category_id") and category is None:
        return await interaction.followup.send("❌ Ticket category no longer exists. Edit the ticket panel settings.", ephemeral=True)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True)
    }
    if guild.me:
        overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True, manage_messages=True, attach_files=True, embed_links=True)
    if role:
        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True)

    clean = "".join(c for c in user.name.lower() if c.isalnum() or c in "-_") or "user"
    try:
        channel = await guild.create_text_channel(name=f"ticket-{clean}-{user.id}"[:95], category=category, overwrites=overwrites, reason=f"Support ticket opened by {user}")
        content = format_ticket_text(panel.get("ticket_open_message", ""), user, role)
        await channel.send(content=content, embed=build_ticket_embed(panel, button_label, user, answers), view=TicketControlView())
    except discord.Forbidden:
        return await interaction.followup.send("❌ Bot needs **Manage Channels** and **Send Messages** permissions.", ephemeral=True)
    except Exception as e:
        print(f"[TICKET CREATE ERROR] {e}")
        return await interaction.followup.send("❌ Failed to create the ticket.", ephemeral=True)
    await interaction.followup.send(f"✅ Your ticket has been created: {channel.mention}", ephemeral=True)


class DynamicTicketModal(discord.ui.Modal):
    def __init__(self, panel_id, button_id, button_label, questions):
        super().__init__(title=f"{button_label[:35]} Ticket")
        self.panel_id = panel_id
        self.button_id = button_id
        self.button_label = button_label
        self.question_inputs = []
        for q in questions[:4]:
            inp = discord.ui.TextInput(label=q[:45], placeholder="Type your answer here...", style=discord.TextStyle.paragraph, required=False, max_length=500)
            self.question_inputs.append((q, inp))
            self.add_item(inp)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        answers = [(q, inp.value.strip()) for q, inp in self.question_inputs]
        await create_ticket(interaction, self.panel_id, self.button_id, self.button_label, [q for q, _ in answers], answers)


class DynamicTicketButton(discord.ui.Button):
    def __init__(self, panel_id, custom_id, label, questions):
        super().__init__(style=discord.ButtonStyle.primary, label=label[:80], custom_id=f"ticket:{panel_id}:{custom_id}")
        self.panel_id = panel_id
        self.button_id = custom_id
        self.questions = questions

    async def callback(self, interaction):
        if self.questions:
            return await interaction.response.send_modal(DynamicTicketModal(self.panel_id, self.button_id, self.label, self.questions))
        await interaction.response.defer(ephemeral=True)
        await create_ticket(interaction, self.panel_id, self.button_id, self.label, [])


class DynamicTicketButtonView(discord.ui.View):
    def __init__(self, panel_id, buttons_data):
        super().__init__(timeout=None)
        for cid, data in list(buttons_data.items())[:25]:
            self.add_item(DynamicTicketButton(panel_id, cid, data["label"], data.get("questions", [])[:4]))


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="close_ticket_btn")
    async def close_ticket(self, interaction, button):
        # Reason is compulsory; the channel is not deleted until the modal is submitted.
        await interaction.response.send_modal(CloseTicketModal())


class CloseTicketModal(discord.ui.Modal, title="Close Ticket"):
    reason = discord.ui.TextInput(label="Closing Reason", placeholder="Why is this ticket being closed?", style=discord.TextStyle.paragraph, required=True, max_length=1000)

    async def on_submit(self, interaction):
        # FIX APPLIED HERE: Defer first to prevent 3-second interaction timeout error
        await interaction.response.defer(ephemeral=True)
        
        channel = interaction.channel
        guild = interaction.guild
        closer = interaction.user
        close_reason = self.reason.value.strip()
        messages = [m async for m in channel.history(limit=None, oldest_first=True)]
        content = (
            f"Ticket Transcript: #{channel.name}\n"
            f"Server: {guild.name if guild else 'Unknown'}\n"
            f"Closed By: {closer} ({closer.id})\n"
            f"Closed At: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"Reason: {close_reason}\n\n"
        )
        for m in messages:
            parts = []
            if m.content:
                parts.append(m.content)
            if m.attachments:
                parts.extend(f"[Attachment] {a.url}" for a in m.attachments)
            if not parts:
                parts.append("[Embed/Component]")
            content += f"[{m.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}] {m.author} ({m.author.id}): {' | '.join(parts)}\n"
        path = f"transcript_{channel.id}.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        file_obj = discord.File(path, filename=f"{channel.name}_transcript.txt")
        try:
            await closer.send(content=(f"🔒 **Ticket Closed**\n**Server:** {guild.name}\n**Closed By:** {closer.mention}\n**Reason:** {close_reason}\n\nYour transcript is attached."), file=file_obj)
        except Exception as e:
            print(f"[TRANSCRIPT DM ERROR] {e}")
        try:
            if ticket_log_channel_id and guild:
                log_chan = guild.get_channel(ticket_log_channel_id)
                if log_chan:
                    await log_chan.send(content=f"📁 **Closed Ticket:** `#{channel.name}`\n**Closed By:** {closer.mention}\n**Reason:** {close_reason}", file=discord.File(path, filename=f"{channel.name}_transcript.txt"))
        except Exception as e:
            print(f"[TRANSCRIPT LOG ERROR] {e}")
        try:
            os.remove(path)
        except Exception:
            pass
        await asyncio.sleep(3)
        
        # FIX APPLIED HERE: Use followup.send since interaction was deferred
        try:
            await interaction.followup.send("🔒 Ticket is being closed. The transcript will be sent by DM if possible.", ephemeral=True)
        except Exception:
            pass

        try:
            await channel.delete(reason=f"Ticket closed by {closer}: {close_reason}")
        except Exception as e:
            print(f"[CLOSE ERROR] {e}")


class ManagerBaseView(discord.ui.View):
    def __init__(self, guild_id, panel_id=None):
        super().__init__(timeout=900)
        self.guild_id = guild_id
        self.panel_id = panel_id

    def panel(self):
        return get_panel(self.guild_id, self.panel_id)


class PanelSelect(discord.ui.Select):
    def __init__(self, guild_id, action):
        self.guild_id = guild_id
        self.action = action
        data = ensure_ticket_structure(guild_id) or {}
        options = [discord.SelectOption(label=(p.get("title") or "Untitled")[:100], value=pid, description=f"{len(p.get('buttons', {}))} button(s)") for pid, p in data.get("panels", {}).items()][:25]
        super().__init__(placeholder="Select a ticket panel...", options=options or [discord.SelectOption(label="No panels", value="none")])

    async def callback(self, interaction):
        if self.values[0] == "none":
            return await interaction.response.send_message("❌ No ticket panels exist.", ephemeral=True)
        self.view.panel_id = self.values[0]
        await interaction.response.edit_message(embed=manager_embed(interaction.guild.id, self.values[0]), view=TicketManagerView(interaction.guild.id, self.values[0]))


def manager_embed(guild_id, panel_id=None):
    data = ensure_ticket_structure(guild_id)
    panel = get_panel(guild_id, panel_id)
    if not data or not panel:
        return discord.Embed(title="🎫 Ticket Manager", description="No ticket panels configured.", color=discord.Color.blue())
    buttons = panel.get("buttons", {})
    button_text = "\n".join(f"• **{d['label']}** — {len(d.get('questions', []))} question(s)" for d in buttons.values()) or "No buttons"
    return discord.Embed(title="🎫 Ticket Manager", description=(f"**Panel:** {panel.get('title','Untitled')}\n**Category:** <#{panel.get('category_id')}>\n**Support Role:** <@&{panel.get('role_id')}>\n\n**Buttons**\n{button_text}\n\nUse the buttons below to edit this panel. Use **Switch Panel** to edit another panel."), color=discord.Color(int(panel.get("ticket_color", 3447003))))


class TicketManagerView(ManagerBaseView):
    @discord.ui.button(label="Switch Panel", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def switch_panel(self, interaction, button):
        await interaction.response.edit_message(view=PanelSelectView(self.guild_id, self.panel_id))

    @discord.ui.button(label="Edit Panel", style=discord.ButtonStyle.primary, emoji="📝")
    async def edit_panel(self, interaction, button):
        panel = self.panel()
        await interaction.response.send_modal(EditPanelModal(self.guild_id, self.panel_id, panel))

    @discord.ui.button(label="Edit Button", style=discord.ButtonStyle.primary, emoji="✏️")
    async def edit_button(self, interaction, button):
        await interaction.response.edit_message(view=ButtonSelectView(self.guild_id, self.panel_id, "edit"))

    @discord.ui.button(label="Add Button", style=discord.ButtonStyle.success, emoji="➕")
    async def add_button(self, interaction, button):
        await interaction.response.send_modal(AddMoreButtonModal(self.guild_id, self.panel_id))

    @discord.ui.button(label="Remove Button", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def remove_button(self, interaction, button):
        await interaction.response.edit_message(view=ButtonSelectView(self.guild_id, self.panel_id, "remove"))

    @discord.ui.button(label="Ticket Message", style=discord.ButtonStyle.secondary, emoji="🎨")
    async def ticket_message(self, interaction, button):
        await interaction.response.send_modal(TicketMessageModal(self.guild_id, self.panel_id))

    @discord.ui.button(label="Deploy Panel", style=discord.ButtonStyle.success, emoji="🚀")
    async def deploy(self, interaction, button):
        panel = self.panel()
        if not panel or not panel.get("buttons"):
            return await interaction.response.send_message("❌ Add at least one button first.", ephemeral=True)
        embed = discord.Embed(title=panel.get("title", "Support Hub"), description=panel.get("desc", ""), color=discord.Color(int(panel.get("ticket_color", 3447003))))
        await interaction.channel.send(embed=embed, view=DynamicTicketButtonView(self.panel_id, panel["buttons"]))
        await interaction.response.send_message("✅ Ticket panel deployed successfully.", ephemeral=True)


class PanelSelectView(discord.ui.View):
    def __init__(self, guild_id, panel_id=None):
        super().__init__(timeout=900)
        self.add_item(PanelSelect(guild_id, "select"))
        self.add_item(BackToManagerButton(guild_id, panel_id))


class BackToManagerButton(discord.ui.Button):
    def __init__(self, guild_id, panel_id):
        super().__init__(label="Back", style=discord.ButtonStyle.secondary, emoji="↩️")
        self.guild_id = guild_id
        self.panel_id = panel_id
    async def callback(self, interaction):
        await interaction.response.edit_message(embed=manager_embed(self.guild_id, self.panel_id), view=TicketManagerView(self.guild_id, self.panel_id))


class ButtonSelect(discord.ui.Select):
    def __init__(self, guild_id, panel_id, action):
        self.guild_id, self.panel_id, self.action = guild_id, panel_id, action
        panel = get_panel(guild_id, panel_id) or {}
        options = [discord.SelectOption(label=d["label"][:100], value=cid) for cid, d in panel.get("buttons", {}).items()][:25]
        super().__init__(placeholder="Select a button...", options=options or [discord.SelectOption(label="No buttons", value="none")])
    async def callback(self, interaction):
        cid = self.values[0]
        if cid == "none":
            return await interaction.response.send_message("❌ No buttons available.", ephemeral=True)
        if self.action == "edit":
            return await interaction.response.send_modal(EditButtonModal(self.guild_id, self.panel_id, cid))
        panel = get_panel(self.guild_id, self.panel_id)
        label = panel["buttons"][cid]["label"]
        del panel["buttons"][cid]
        save_persistent_settings()
        await interaction.response.edit_message(embed=manager_embed(self.guild_id, self.panel_id), view=TicketManagerView(self.guild_id, self.panel_id))
        await interaction.followup.send(f"🗑️ Button **{label}** removed.", ephemeral=True)


class ButtonSelectView(discord.ui.View):
    def __init__(self, guild_id, panel_id, action):
        super().__init__(timeout=900)
        self.add_item(ButtonSelect(guild_id, panel_id, action))
        self.add_item(BackToManagerButton(guild_id, panel_id))


class AddMoreButtonModal(discord.ui.Modal):
    def __init__(self, guild_id, panel_id):
        super().__init__(title="Add Ticket Button")
        self.guild_id, self.panel_id = guild_id, panel_id
        self.btn_name = discord.ui.TextInput(label="Button Name", required=True, max_length=50)
        self.questions = [discord.ui.TextInput(label=f"Question {i+1}", required=False, max_length=100) for i in range(4)]
        self.add_item(self.btn_name)
        for q in self.questions: self.add_item(q)
    async def on_submit(self, interaction):
        panel = get_panel(self.guild_id, self.panel_id)
        if not panel: return await interaction.response.send_message("❌ Panel not found.", ephemeral=True)
        cid = f"custom_ticket_{random.randint(100000, 999999)}"
        panel.setdefault("buttons", {})[cid] = {"label": self.btn_name.value.strip(), "questions": [q.value.strip() for q in self.questions if q.value.strip()]}
        save_persistent_settings()
        await interaction.response.send_message("✅ Button added.", ephemeral=True)


class EditButtonModal(discord.ui.Modal):
    def __init__(self, guild_id, panel_id, cid):
        data = get_panel(guild_id, panel_id)["buttons"][cid]
        super().__init__(title="Edit Ticket Button")
        self.guild_id, self.panel_id, self.cid = guild_id, panel_id, cid
        self.btn_name = discord.ui.TextInput(label="Button Name", default=data["label"][:50], max_length=50)
        old = data.get("questions", []) + [""] * 4
        self.questions = [discord.ui.TextInput(label=f"Question {i+1}", default=old[i][:100], required=False, max_length=100) for i in range(4)]
        self.add_item(self.btn_name)
        for q in self.questions: self.add_item(q)
    async def on_submit(self, interaction):
        panel = get_panel(self.guild_id, self.panel_id)
        panel["buttons"][self.cid] = {"label": self.btn_name.value.strip(), "questions": [q.value.strip() for q in self.questions if q.value.strip()]}
        save_persistent_settings()
        await interaction.response.send_message("✅ Button updated.", ephemeral=True)


class EditPanelModal(discord.ui.Modal):
    def __init__(self, guild_id, panel_id, panel):
        super().__init__(title="Edit Ticket Panel")
        self.guild_id, self.panel_id = guild_id, panel_id
        self.title_input = discord.ui.TextInput(label="Panel Title", default=panel.get("title", "Support Hub")[:100], max_length=100)
        self.desc_input = discord.ui.TextInput(label="Panel Description", default=panel.get("desc", "")[:4000], style=discord.TextStyle.paragraph, max_length=4000)
        self.add_item(self.title_input); self.add_item(self.desc_input)
    async def on_submit(self, interaction):
        panel = get_panel(self.guild_id, self.panel_id)
        panel["title"], panel["desc"] = self.title_input.value, self.desc_input.value
        save_persistent_settings()
        await interaction.response.send_message("✅ Panel title and description updated.", ephemeral=True)


class TicketMessageModal(discord.ui.Modal):
    def __init__(self, guild_id, panel_id):
        panel = get_panel(guild_id, panel_id)
        super().__init__(title="Edit Ticket Message")
        self.guild_id, self.panel_id = guild_id, panel_id
        self.title_input = discord.ui.TextInput(label="Ticket Title", default=panel.get("ticket_title", "🎫 {type} Ticket")[:256], max_length=256)
        self.desc_input = discord.ui.TextInput(label="Ticket Description", default=panel.get("ticket_desc", "")[:4000], style=discord.TextStyle.paragraph, max_length=4000)
        self.open_input = discord.ui.TextInput(label="Opening Message", default=panel.get("ticket_open_message", "")[:1000], required=False, max_length=1000)
        self.footer_input = discord.ui.TextInput(label="Footer", default=panel.get("ticket_footer", "")[:2048], required=False, max_length=2048)
        self.color_input = discord.ui.TextInput(label="Embed Color (hex)", default=f"{int(panel.get('ticket_color', 3447003)):06X}", required=False, max_length=6)
        for x in (self.title_input, self.desc_input, self.open_input, self.footer_input, self.color_input): self.add_item(x)
    async def on_submit(self, interaction):
        panel = get_panel(self.guild_id, self.panel_id)
        panel["ticket_title"], panel["ticket_desc"] = self.title_input.value, self.desc_input.value
        panel["ticket_open_message"], panel["ticket_footer"] = self.open_input.value, self.footer_input.value
        try: panel["ticket_color"] = int(self.color_input.value.replace("#", ""), 16)
        except ValueError: panel["ticket_color"] = 3447003
        save_persistent_settings()
        await interaction.response.send_message("✅ Ticket title, description, opening message, footer and color updated.", ephemeral=True)


class TicketManagerStartView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=900)
        self.guild_id = guild_id
        self.add_item(PanelSelect(guild_id, "start"))


class TicketSetupModal(discord.ui.Modal):
    def __init__(self, category, role):
        super().__init__(title="Create Ticket Panel")
        self.category, self.role = category, role
        self.panel_title = discord.ui.TextInput(label="Panel Title", default="Support Hub", max_length=100)
        self.panel_desc = discord.ui.TextInput(label="Panel Description", default="Click a button below to open a support ticket.", style=discord.TextStyle.paragraph, max_length=4000)
        self.btn_name = discord.ui.TextInput(label="First Button Name", default="Support", max_length=50)
        self.questions = [discord.ui.TextInput(label=f"Question {i+1}", required=False, max_length=100) for i in range(4)]
        for x in (self.panel_title, self.panel_desc, self.btn_name, *self.questions): self.add_item(x)
    async def on_submit(self, interaction):
        guild = interaction.guild
        if not guild: return await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        data = ensure_ticket_structure(guild.id)
        if not data:
            pid = f"panel_{random.randint(100000, 999999)}"
            panel = new_ticket_panel(self.category, self.role, self.panel_title.value, self.panel_desc.value)
            first = next(iter(panel["buttons"]))
            panel["buttons"][first] = {"label": self.btn_name.value, "questions": [q.value.strip() for q in self.questions if q.value.strip()]}
            ticket_configs[guild.id] = {"active_panel": pid, "panels": {pid: panel}}
        else:
            pid = f"panel_{random.randint(100000, 999999)}"
            panel = new_ticket_panel(self.category, self.role, self.panel_title.value, self.panel_desc.value)
            first = next(iter(panel["buttons"]))
            panel["buttons"][first] = {"label": self.btn_name.value, "questions": [q.value.strip() for q in self.questions if q.value.strip()]}
            data["panels"][pid] = panel
            data["active_panel"] = pid
        save_persistent_settings()
        await interaction.response.send_message(embed=manager_embed(guild.id, pid), view=TicketManagerView(guild.id, pid), ephemeral=True)


@bot.tree.command(name="setup_ticket", description="Create a new ticket panel")
@app_commands.describe(category="The category where tickets will be created", role="The support role that can view tickets")
@app_permission_check("administrator")
async def setup_ticket(interaction: discord.Interaction, category: discord.CategoryChannel, role: discord.Role):
    await interaction.response.send_modal(TicketSetupModal(category, role))


@bot.hybrid_command(name="edit_ticket", description="Open the ticket panel manager")
@permission_check("administrator")
@app_permission_check("administrator")
async def edit_ticket(ctx: commands.Context):
    if not ctx.guild or not ensure_ticket_structure(ctx.guild.id):
        return await ctx.send("❌ No ticket panels are configured. Run `/setup_ticket` first.", ephemeral=True)
    data = ensure_ticket_structure(ctx.guild.id)
    pid = data.get("active_panel")
    await ctx.send(embed=manager_embed(ctx.guild.id, pid), view=TicketManagerView(ctx.guild.id, pid), ephemeral=True)


@bot.hybrid_command(name="add_ticket_button", description="Add a button to a ticket panel")
@permission_check("administrator")
@app_permission_check("administrator")
async def add_ticket_button(ctx: commands.Context):
    if not ctx.guild or not ensure_ticket_structure(ctx.guild.id):
        return await ctx.send("❌ No ticket panels are configured. Run `/setup_ticket` first.", ephemeral=True)
    pid = ensure_ticket_structure(ctx.guild.id).get("active_panel")
    await ctx.send(embed=manager_embed(ctx.guild.id, pid), view=TicketManagerView(ctx.guild.id, pid), ephemeral=True)
# =========================================================
# GIVEAWAY
# =========================================================

@bot.hybrid_command(
    name="giveaway",
    description="Start an interactive giveaway with optional fixed winner."
)
@app_commands.describe(
    prize="The prize being given away",
    duration="Duration format (e.g. 30s, 10m, 2h, 1d)",
    winners_count="Number of winners to pick",
    fixed_winner="Optional specific member to guarantee as a winner"
)
@permission_check("manage_guild")
@app_permission_check("manage_guild")
async def giveaway(
    ctx: commands.Context,
    prize: str,
    duration: str,
    winners_count: int = 1,
    fixed_winner: discord.Member = None
):

    channel = ctx.channel
    author = ctx.author
    guild = ctx.guild

    seconds = parse_time(duration)

    embed = discord.Embed(
        title="🎉 GIVEAWAY TIME! 🎉",
        description=(
            f"**Prize:** {prize}\n"
            f"**Winner(s):** `{winners_count}`\n"
            f"**Hosted by:** {author.mention}\n\n"
            f"React with 🎉 to enter!"
        ),
        color=discord.Color.gold(),
        timestamp=datetime.now(timezone.utc)
    )

    embed.set_footer(
        text=f"Ends in {duration}"
    )

    msg = await ctx.send(embed=embed)

    await msg.add_reaction("🎉")

    await asyncio.sleep(seconds)

    try:
        msg = await channel.fetch_message(msg.id)
    except Exception:
        return

    reaction = discord.utils.get(
        msg.reactions,
        emoji="🎉"
    )

    participants = []

    if reaction:
        async for u in reaction.users():
            if not u.bot:
                participants.append(u.id)

    chosen_winners = []

    if fixed_winner and fixed_winner.id in participants:
        chosen_winners.append(fixed_winner)
        participants.remove(fixed_winner.id)

    while (
        len(chosen_winners) < winners_count
        and participants
    ):
        winner_id = random.choice(participants)

        participants.remove(winner_id)

        member = guild.get_member(winner_id)

        if member:
            chosen_winners.append(member)

    if chosen_winners:

        winners_mention = ", ".join(
            [
                w.mention
                for w in chosen_winners
            ]
        )

        ended_embed = discord.Embed(
            title="🎉 GIVEAWAY ENDED 🎉",
            description=(
                f"**Prize:** {prize}\n"
                f"**Winner(s):** {winners_mention} 🏆\n"
                f"**Hosted by:** {author.mention}"
            ),
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )

        await msg.edit(
            embed=ended_embed,
            view=None
        )

        await channel.send(
            f"🎊 Congratulations {winners_mention}! "
            f"You won **{prize}**!"
        )

    else:

        ended_embed = discord.Embed(
            title="🎉 GIVEAWAY ENDED 🎉",
            description=(
                f"**Prize:** {prize}\n"
                f"❌ No valid participants found."
            ),
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )

        await msg.edit(
            embed=ended_embed,
            view=None
        )


# =========================================================
# ANTI-NUKE SETTINGS
# =========================================================

@bot.hybrid_command(
    name="set_ban_limit",
    description="Set anti-nuke max ban threshold limit."
)
@permission_check("administrator")
@app_permission_check("administrator")
async def set_ban_limit(
    ctx: commands.Context,
    limit: int
):

    global ban_limit

    ban_limit = limit
    save_persistent_settings()

    res = (
        f"✅ **Anti-Nuke Ban Limit updated to:** "
        f"`{ban_limit}` bans / 2 mins"
    )

    await ctx.send(res)


@bot.hybrid_command(
    name="set_channel_limit",
    description="Set anti-nuke max channel delete threshold limit."
)
@permission_check("administrator")
@app_permission_check("administrator")
async def set_channel_limit(
    ctx: commands.Context,
    limit: int
):

    global channel_limit

    channel_limit = limit
    save_persistent_settings()

    await ctx.send(
        f"✅ **Anti-Nuke Channel Delete Limit updated to:** "
        f"`{channel_limit}` channels / 2 mins"
    )


@bot.hybrid_command(
    name="set_spam_limit",
    description="Set max allowed messages within 5 seconds before mute."
)
@permission_check("administrator")
@app_permission_check("administrator")
async def set_spam_limit(
    ctx: commands.Context,
    messages_count: int
):

    global spam_limit

    spam_limit = messages_count
    save_persistent_settings()

    await ctx.send(
        f"✅ **Anti-Spam Limit updated to:** "
        f"`{spam_limit}` msgs / 5 sec"
    )


@bot.hybrid_command(
    name="set_prefix",
    description="Set custom prefix for text commands."
)
@permission_check("administrator")
@app_permission_check("administrator")
async def set_prefix(
    ctx: commands.Context,
    prefix: str
):

    global custom_prefix

    custom_prefix = prefix

    bot.command_prefix = commands.when_mentioned_or(
        custom_prefix
    )
    save_persistent_settings()

    await ctx.send(
        f"✅ **Custom Prefix updated to:** "
        f"`{custom_prefix}`"
    )


# =========================================================
# TICKET SETTINGS
# =========================================================

@bot.hybrid_command(
    name="ticket_log_channel",
    description="Set log channel for closed ticket transcripts."
)
@permission_check("administrator")
@app_permission_check("administrator")
async def ticket_log_channel(
    ctx: commands.Context,
    channel: discord.TextChannel
):

    global ticket_log_channel_id

    ticket_log_channel_id = channel.id
    save_persistent_settings()

    await ctx.send(
        f"✅ **Ticket Transcript Log Channel set to:** "
        f"{channel.mention}"
    )


@bot.hybrid_command(
    name="set_ticket_ping",
    description="Customize ticket opening ping message."
)
@permission_check("administrator")
@app_permission_check("administrator")
async def set_ticket_ping(
    ctx: commands.Context,
    message: str
):

    global custom_ticket_ping

    custom_ticket_ping = message
    save_persistent_settings()

    await ctx.send(
        f"✅ **Ticket Open Ping updated!**\n"
        f"Format: `{message}`"
    )


# =========================================================
# WELCOME
# =========================================================

class SimpleWelcomeModal(
    Modal,
    title="Configure Welcome Message"
):

    wel_msg = TextInput(
        label="Welcome Message",
        style=discord.TextStyle.paragraph,
        default=(
            "Hey {user}, welcome to **{server}**! "
            "Member count: {count}"
        ),
        max_length=1000
    )

    wel_img = TextInput(
        label="Banner Image URL (Optional)",
        required=False,
        placeholder="Paste image link here or leave blank",
        default=""
    )

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        global custom_welcome_msg
        global custom_welcome_img
        global welcome_enabled

        custom_welcome_msg = self.wel_msg.value

        custom_welcome_img = (
            self.wel_img.value.strip()
            if self.wel_img.value
            else None
        )

        welcome_enabled = True

        await interaction.response.send_message(
            f"✅ **Welcome system fully updated!**\n\n"
            f"💬 **Message:** `{custom_welcome_msg}`",
            ephemeral=True
        )


class WelcomeSelectView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="Select Welcome Channel...",
        channel_types=[discord.ChannelType.text],
        min_values=1,
        max_values=1
    )
    async def select_channel(
        self,
        interaction: discord.Interaction,
        select: discord.ui.ChannelSelect
    ):

        selected_channel = select.values[0]

        global welcome_channel_id

        welcome_channel_id = selected_channel.id
        save_persistent_settings()

        await interaction.response.send_modal(
            SimpleWelcomeModal()
        )


@bot.hybrid_command(
    name="setup_welcome",
    description="Configure custom welcome channel, message and banner."
)
@permission_check("administrator")
@app_permission_check("administrator")
async def setup_welcome(
    ctx: commands.Context
):

    view = WelcomeSelectView()

    msg = (
        "📌 **Please select your welcome channel "
        "from the dropdown below:**"
    )

    await ctx.send(
        msg,
        view=view,
        ephemeral=True
    )


@bot.hybrid_command(
    name="disable_welcome",
    description="Turn off welcome system."
)
@permission_check("administrator")
@app_permission_check("administrator")
async def disable_welcome(
    ctx: commands.Context
):

    global welcome_enabled

    welcome_enabled = False
    save_persistent_settings()

    await ctx.send(
        "❌ **Welcome system has been disabled.**"
    )


# =========================================================
# INVITE TRACKER
# =========================================================

@bot.hybrid_command(
    name="setup_invitelog",
    description="Set channel for invite logs."
)
@permission_check("administrator")
@app_permission_check("administrator")
async def setup_invitelog(
    ctx: commands.Context,
    channel: discord.TextChannel = None
):

    global invite_log_channel_id

    target = channel or ctx.channel

    invite_log_channel_id = target.id

    await ctx.send(
        f"✅ **Invite Logger set to:** "
        f"{target.mention}"
    )


@bot.hybrid_command(
    name="invites",
    description="Check invite stats of a server member."
)
async def invites(
    ctx: commands.Context,
    member: discord.Member = None
):

    target = member or ctx.author

    guild = ctx.guild

    total_uses = 0

    try:
        guild_invites = await guild.invites()

        for inv in guild_invites:

            if (
                inv.inviter
                and inv.inviter.id == target.id
            ):
                total_uses += inv.uses

    except Exception:
        pass

    embed = discord.Embed(
        title=f"Invite Stats: {target.display_name}",
        description=(
            f"👤 **Member:** {target.mention}\n"
            f"📈 **Total Invites:** `{total_uses}`"
        ),
        color=discord.Color.blue()
    )

    await ctx.send(
        embed=embed
    )


# =========================================================
# MODERATION
# =========================================================

@bot.hybrid_command(
    name="ban",
    description="Permanently ban a member."
)
@app_commands.describe(
    member="The member to ban",
    reason="Reason for the ban"
)
@permission_check("ban_members")
@app_permission_check("ban_members")
async def ban_cmd(
    ctx: commands.Context,
    member: discord.Member,
    reason: str = "No reason provided"
):

    guild = ctx.guild

    hierarchy = _hierarchy_error(
        ctx,
        member
    )

    if hierarchy:
        await ctx.send(hierarchy)
        return

    if (
        is_whitelisted(member, guild)
        and member.id != ctx.author.id
    ):
        await ctx.send(
            "❌ **Access Denied:** User is whitelisted.",
            ephemeral=True
        )
        return

    await member.ban(
        reason=reason
    )

    await ctx.send(
        f"🔨 **{member.mention} banned successfully!**"
    )


@bot.hybrid_command(
    name="mute",
    description="Timeout a member for a specified duration."
)
@app_commands.describe(
    member="The member to mute",
    duration="Duration (e.g. 10m, 1h)",
    reason="Reason for timeout"
)
@permission_check("moderate_members")
@app_permission_check("moderate_members")
async def mute_cmd(
    ctx: commands.Context,
    member: discord.Member,
    duration: str,
    reason: str = "No reason provided"
):

    guild = ctx.guild

    hierarchy = _hierarchy_error(
        ctx,
        member
    )

    if hierarchy:
        await ctx.send(hierarchy)
        return

    if (
        is_whitelisted(member, guild)
        and member.id != ctx.author.id
    ):
        await ctx.send(
            "❌ **Access Denied:** User is whitelisted.",
            ephemeral=True
        )
        return

    seconds = parse_time(duration)

    await member.timeout(
        timedelta(seconds=seconds),
        reason=reason
    )

    await ctx.send(
        f"🤐 **{member.mention} timed out for {duration}!**"
    )


@bot.hybrid_command(
    name="removetimeout",
    aliases=["rto"],
    description="Remove a member's timeout."
)
@app_commands.describe(member="The member whose timeout you want to remove", reason="Reason for removing timeout")
@permission_check("moderate_members")
@app_permission_check("moderate_members")
async def removetimeout_cmd(
    ctx: commands.Context,
    member: discord.Member,
    reason: str = "No reason provided"
):
    guild = ctx.guild
    hierarchy = _hierarchy_error(ctx, member)
    if hierarchy:
        await ctx.send(hierarchy)
        return

    if is_whitelisted(member, guild) and member.id != ctx.author.id:
        await ctx.send(
            "❌ **Access Denied:** User is whitelisted.",
            ephemeral=True
        )
        return

    if not member.is_timed_out():
        await ctx.send(f"ℹ️ {member.mention} is not currently timed out.")
        return

    try:
        await member.timeout(None, reason=reason)
        await ctx.send(f"🔓 **Timeout removed successfully from {member.mention}!**")
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to remove this member's timeout.", ephemeral=True)
    except Exception as e:
        print(f"[REMOVE TIMEOUT ERROR] {e}")
        await ctx.send("❌ Timeout remove karte waqt error aa gaya.", ephemeral=True)


@bot.hybrid_command(
    name="lock",
    description="Lock the current channel for regular members."
)
@permission_check("manage_channels")
@app_permission_check("manage_channels")
async def lock_channel(ctx: commands.Context):
    channel = ctx.channel
    if not isinstance(channel, discord.TextChannel):
        return await ctx.send("❌ This command can only be used in a text channel.", ephemeral=True)

    try:
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = False
        await channel.set_permissions(
            ctx.guild.default_role,
            overwrite=overwrite,
            reason=f"Channel locked by {ctx.author}"
        )
        await ctx.send("🔒 **Channel locked!** Regular members can no longer send messages here.")
    except discord.Forbidden:
        await ctx.send("❌ I need **Manage Channels** permission to lock this channel.", ephemeral=True)
    except Exception as e:
        print(f"[LOCK ERROR] {e}")
        await ctx.send("❌ Channel lock karte waqt error aa gaya.", ephemeral=True)


@bot.hybrid_command(
    name="unlock",
    description="Unlock the current channel for regular members."
)
@permission_check("manage_channels")
@app_permission_check("manage_channels")
async def unlock_channel(ctx: commands.Context):
    channel = ctx.channel
    if not isinstance(channel, discord.TextChannel):
        return await ctx.send("❌ This command can only be used in a text channel.", ephemeral=True)

    try:
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = None
        await channel.set_permissions(
            ctx.guild.default_role,
            overwrite=overwrite,
            reason=f"Channel unlocked by {ctx.author}"
        )
        await ctx.send("🔓 **Channel unlocked!** Regular members can send messages again.")
    except discord.Forbidden:
        await ctx.send("❌ I need **Manage Channels** permission to unlock this channel.", ephemeral=True)
    except Exception as e:
        print(f"[UNLOCK ERROR] {e}")
        await ctx.send("❌ Channel unlock karte waqt error aa gaya.", ephemeral=True)


@bot.hybrid_command(
    name="purge",
    description="Bulk delete messages in current channel."
)
@app_commands.describe(
    amount="Number of messages to delete (1-100)"
)
@permission_check("manage_messages")
@app_permission_check("manage_messages")
async def purge_cmd(
    ctx: commands.Context,
    amount: int
):

    channel = ctx.channel

    if amount < 1 or amount > 100:
        await ctx.send(
            "❌ Please specify an amount between 1 and 100.",
            ephemeral=True
        )
        return

    if ctx.interaction:

        await ctx.interaction.response.defer(
            ephemeral=True
        )

        deleted = await channel.purge(
            limit=amount
        )

        await ctx.interaction.followup.send(
            f"🧹 Successfully deleted "
            f"**{len(deleted)}** messages!",
            ephemeral=True
        )

    else:

        deleted = await channel.purge(
            limit=amount
        )

        await ctx.send(
            f"🧹 Successfully deleted "
            f"**{len(deleted)}** messages!",
            delete_after=5
        )


@bot.hybrid_command(
    name="role",
    description="Assign or remove a role from a member easily."
)
@app_commands.describe(
    action="add or remove",
    member="Target member",
    role="Target role"
)
@permission_check("manage_roles")
@app_permission_check("manage_roles")
async def role_cmd(
    ctx: commands.Context,
    action: Literal["add", "remove"],
    member: discord.Member,
    role: discord.Role
):

    user = ctx.author

    bot_member = ctx.guild.me

    if bot_member and role >= bot_member.top_role:
        return await ctx.send(
            "❌ I cannot manage a role equal to or higher than my highest role."
        )

    if (
        user.id != OWNER_ID
        and role >= user.top_role
    ):
        return await ctx.send(
            "❌ You cannot manage a role equal to or higher than your highest role."
        )

    if (
        member.id != user.id
        and user.id != OWNER_ID
        and member.top_role >= user.top_role
    ):
        return await ctx.send(
            "❌ You cannot manage a member with an equal or higher role than yours."
        )

    if action == "add":

        await member.add_roles(
            role,
            reason=f"Managed by {user}"
        )

        res = (
            f"✅ Successfully added **{role.name}** "
            f"to {member.mention}!"
        )

    else:

        await member.remove_roles(
            role,
            reason=f"Managed by {user}"
        )

        res = (
            f"✅ Successfully removed **{role.name}** "
            f"from {member.mention}!"
        )

    await ctx.send(res)


# =========================================================
# DM ALL
# =========================================================

@bot.hybrid_command(
    name="dmall",
    description="Send DM announcement to all server members."
)
@app_commands.describe(
    message="The message you want to broadcast",
    as_embed="True for Embed format, False for Plain Text"
)
@permission_check("administrator")
@app_permission_check("administrator")
async def dmall(
    ctx: commands.Context,
    message: str,
    as_embed: bool = False
):

    guild = ctx.guild

    format_type = (
        "Embed"
        if as_embed
        else "Plain Text"
    )

    await ctx.send(
        f"⏳ **Starting DM Broadcast "
        f"({format_type})...** Safe delay active."
    )

    success_count = 0
    failed_count = 0

    for member in guild.members:

        if member.bot:
            continue

        try:

            if as_embed:

                embed = discord.Embed(
                    title=f"Announcement from {guild.name}",
                    description=message,
                    color=discord.Color.gold()
                )

                await member.send(
                    embed=embed
                )

            else:

                await member.send(
                    content=message
                )

            success_count += 1

            await asyncio.sleep(1.5)

        except Exception:

            failed_count += 1

    await ctx.send(
        f"✅ Sent ({format_type}): "
        f"{success_count} | "
        f"❌ Failed: {failed_count}"
    )


# =========================================================
# MEMBER JOIN
# =========================================================

@bot.event
async def on_member_join(member):

    guild = member.guild

    inviter_user = None

    try:

        old_invites = invites_cache.get(
            guild.id,
            []
        )

        new_invites = await guild.invites()

        invites_cache[guild.id] = new_invites

        for old_inv in old_invites:

            new_inv = discord.utils.get(
                new_invites,
                code=old_inv.code
            )

            if (
                new_inv
                and new_inv.uses > old_inv.uses
            ):
                inviter_user = old_inv.inviter
                break

    except Exception:
        pass

    global invite_log_channel_id

    if invite_log_channel_id:

        log_channel = guild.get_channel(
            invite_log_channel_id
        )

        if log_channel:

            inviter_str = (
                inviter_user.mention
                if inviter_user
                else "Unknown Link"
            )

            embed = discord.Embed(
                title="📥 Member Joined via Invite",
                description=(
                    f"**Member:** {member.mention}\n"
                    f"**Invited By:** {inviter_str}"
                ),
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc)
            )

            embed.set_thumbnail(
                url=member.display_avatar.url
            )

            await log_channel.send(
                embed=embed
            )

    global welcome_enabled
    global welcome_channel_id
    global custom_welcome_msg
    global custom_welcome_img

    if not welcome_enabled:
        return

    target_channel = (
        guild.get_channel(welcome_channel_id)
        if welcome_channel_id
        else guild.system_channel
    )

    if target_channel:

        if custom_welcome_msg:

            description_text = custom_welcome_msg.format(
                user=member.mention,
                server=guild.name,
                count=guild.member_count,
                inviter=(
                    inviter_user.name
                    if inviter_user
                    else "Unknown"
                )
            )

        else:

            description_text = (
                f"Hey {member.mention}, "
                f"welcome to **{guild.name}**!"
            )

        embed = discord.Embed(
            title="Welcome!",
            description=description_text,
            color=discord.Color.blue()
        )

        embed.set_thumbnail(
            url=member.display_avatar.url
        )

        if custom_welcome_img:
            embed.set_image(
                url=custom_welcome_img
            )

        embed.set_footer(
            text=f"Member #{guild.member_count}"
        )

        await target_channel.send(
            content=f"Welcome {member.mention}!",
            embed=embed
        )


# =========================================================
# INFORMATION COMMANDS
# =========================================================

@bot.hybrid_command(name="serverinfo", description="Show information about the current server.")
async def serverinfo(ctx):
    guild = ctx.guild
    if not guild: return await ctx.send("❌ This command can only be used inside a server.")
    embed = discord.Embed(title=f"Server Information — {guild.name}", color=discord.Color.blue())
    if guild.icon: embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="Owner", value=guild.owner.mention if guild.owner else "Unknown")
    embed.add_field(name="Server ID", value=str(guild.id))
    embed.add_field(name="Members", value=str(guild.member_count))
    embed.add_field(name="Roles", value=str(len(guild.roles)))
    embed.add_field(name="Channels", value=str(len(guild.channels)))
    embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, "F"), inline=False)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="roleinfo", description="List all roles in the current server.")
async def roleinfo(ctx):
    guild = ctx.guild
    if not guild: return await ctx.send("❌ This command can only be used inside a server.")
    roles = list(reversed(guild.roles))
    lines = [f"{r.mention} — ID: `{r.id}` — {len(r.members)} member(s)" for r in roles]
    if not lines: return await ctx.send("This server has no roles.")
    chunks, current = [], ""
    for line in lines:
        if len(current) + len(line) + 1 > 3900:
            chunks.append(current); current = ""
        current += line + "\n"
    if current: chunks.append(current)
    for i, chunk in enumerate(chunks):
        embed = discord.Embed(title=f"Roles in {guild.name} ({len(guild.roles)})", description=chunk, color=discord.Color.blue())
        embed.set_footer(text=f"Page {i+1}/{len(chunks)}")
        await ctx.send(embed=embed)


@bot.hybrid_command(name="userinfo", description="Show information about a server member.")
@app_commands.describe(member="The member to inspect")
async def userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed = discord.Embed(title=f"User Information — {member}", color=member.color if member.color.value else discord.Color.blue())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Username", value=str(member))
    embed.add_field(name="User ID", value=str(member.id))
    embed.add_field(name="Bot", value="Yes" if member.bot else "No")
    embed.add_field(name="Account Created", value=discord.utils.format_dt(member.created_at, "F"), inline=False)
    embed.add_field(name="Joined Server", value=discord.utils.format_dt(member.joined_at, "F") if member.joined_at else "Unknown", inline=False)
    roles = [r.mention for r in member.roles[1:]]
    embed.add_field(name="Roles", value=", ".join(roles)[:1024] or "None")
    await ctx.send(embed=embed)


@bot.hybrid_command(name="channelinfo", description="Show information about a channel.")
@app_commands.describe(channel="The channel to inspect")
async def channelinfo(ctx, channel: discord.TextChannel = None):
    channel = channel or ctx.channel
    embed = discord.Embed(title=f"Channel Information — #{channel.name}", color=discord.Color.blue())
    embed.add_field(name="Channel ID", value=str(channel.id))
    embed.add_field(name="Type", value=str(channel.type).replace("ChannelType.", "").title())
    embed.add_field(name="Category", value=channel.category.mention if getattr(channel, "category", None) else "None")
    embed.add_field(name="Position", value=str(getattr(channel, "position", "Unknown")))
    if hasattr(channel, "topic"): embed.add_field(name="Topic", value=channel.topic or "None", inline=False)
    embed.add_field(name="Created", value=discord.utils.format_dt(channel.created_at, "F"), inline=False)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="avatar", description="Show a member's avatar.")
@app_commands.describe(member="The member whose avatar you want to view")
async def avatar(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed = discord.Embed(title=f"{member.display_name}'s Avatar", color=discord.Color.blue())
    embed.set_image(url=member.display_avatar.url)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="servericon", description="Show the current server icon.")
async def servericon(ctx):
    if not ctx.guild: return await ctx.send("❌ This command can only be used inside a server.")
    if not ctx.guild.icon: return await ctx.send("❌ This server does not have a server icon.")
    embed = discord.Embed(title=f"{ctx.guild.name} — Server Icon", color=discord.Color.blue())
    embed.set_image(url=ctx.guild.icon.url)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="membercount", description="Show the current server member count.")
async def membercount(ctx):
    if not ctx.guild: return await ctx.send("❌ This command can only be used inside a server.")
    await ctx.send(f"👥 **{ctx.guild.name}** has **{ctx.guild.member_count:,}** members.")


@bot.hybrid_command(name="permissions", description="Show a member's server permissions.")
@app_commands.describe(member="The member whose permissions you want to inspect")
async def permissions(ctx, member: discord.Member = None):
    if not ctx.guild: return await ctx.send("❌ This command can only be used inside a server.")
    member = member or ctx.author
    perms = [name.replace("_", " ").title() for name, value in member.guild_permissions if value]
    text = "\n".join(f"• {p}" for p in perms) or "None"
    embed = discord.Embed(title=f"Permissions — {member}", description=text[:4096], color=discord.Color.green())
    embed.set_footer(text=f"User ID: {member.id}")
    await ctx.send(embed=embed)



# =========================================================
# HELP COMMANDS
# =========================================================

@bot.hybrid_command(
    name="antinuke_help",
    description="Show all Anti-Nuke configuration commands."
)
async def help_antinuke(
    ctx: commands.Context
):

    embed = discord.Embed(
        title="🛡️ Anti-Nuke & Anti-Spam Commands",
        color=discord.Color.red()
    )

    embed.add_field(
        name="/set_ban_limit <limit>",
        value="Set max ban limit threshold",
        inline=False
    )

    embed.add_field(
        name="/set_channel_limit <limit>",
        value="Set max channel deletion limit",
        inline=False
    )

    embed.add_field(
        name="/set_spam_limit <msgs>",
        value="Set message spam speed threshold",
        inline=False
    )

    await ctx.send(
        embed=embed
    )


@bot.hybrid_command(
    name="ticket_help",
    description="Show all Ticket Panel management commands."
)
async def help_ticket(
    ctx: commands.Context
):

    embed = discord.Embed(
        title="🎫 Ticket System Commands",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="/setup_ticket",
        value=(
            "Interactive wizard to set category, "
            "role, title & buttons"
        ),
        inline=False
    )

    embed.add_field(
        name="/edit_ticket",
        value="Open the single ticket manager and edit existing panels, buttons, questions and ticket messages",
        inline=False
    )

    embed.add_field(
        name="/add_ticket_button",
        value=(
            "Open the ticket manager to add a button with up to 4 optional questions"
        ),
        inline=False
    )

    embed.add_field(
        name="/ticket_log_channel <channel>",
        value="Set channel for transcripts",
        inline=False
    )

    embed.add_field(
        name="/set_ticket_ping <msg>",
        value="Set custom mention message",
        inline=False
    )

    await ctx.send(
        embed=embed
    )


@bot.hybrid_command(
    name="giveaway_help",
    description="Show all Giveaway system commands."
)
async def help_giveaway(
    ctx: commands.Context
):

    embed = discord.Embed(
        title="🎉 Giveaway System Commands",
        color=discord.Color.gold()
    )

    embed.add_field(
        name="/giveaway <prize> <duration> [winners] [fixed_winner]",
        value=(
            "Start giveaway with optional fixed winner"
        ),
        inline=False
    )

    await ctx.send(
        embed=embed
    )


@bot.hybrid_command(
    name="welcome_help",
    description="Show all Welcome System commands."
)
async def help_welcome(
    ctx: commands.Context
):

    embed = discord.Embed(
        title="👋 Welcome System Commands",
        color=discord.Color.green()
    )

    embed.add_field(
        name="/setup_welcome",
        value=(
            "Set welcome channel, message, "
            "and banner via interactive modal"
        ),
        inline=False
    )

    embed.add_field(
        name="/disable_welcome",
        value="Turn off welcome system",
        inline=False
    )

    await ctx.send(
        embed=embed
    )


@bot.hybrid_command(
    name="invites_help",
    description="Show all Invite Tracker commands."
)
async def help_invites(
    ctx: commands.Context
):

    embed = discord.Embed(
        title="📊 Invite Tracker Commands",
        color=discord.Color.gold()
    )

    embed.add_field(
        name="/setup_invitelog [channel]",
        value="Set invite logging channel",
        inline=False
    )

    embed.add_field(
        name="/invites [member]",
        value="Check member invite count",
        inline=False
    )

    await ctx.send(
        embed=embed
    )


@bot.hybrid_command(
    name="moderation_help",
    description="Show all Moderation commands."
)
async def help_mod(
    ctx: commands.Context
):

    embed = discord.Embed(
        title="🔨 Moderation Commands",
        color=discord.Color.purple()
    )

    embed.add_field(
        name="/ban <member> [reason]",
        value="Permanently ban a member",
        inline=False
    )

    embed.add_field(
        name="/mute <member> <time> [reason]",
        value="Timeout member (e.g. 10m, 1h)",
        inline=False
    )

    embed.add_field(
        name="/removetimeout <member> [reason]",
        value="Remove a member's timeout. Prefix aliases: ,removetimeout / ,rto",
        inline=False
    )

    embed.add_field(
        name="/lock",
        value="Lock the current channel. Prefix: ,lock",
        inline=False
    )

    embed.add_field(
        name="/unlock",
        value="Unlock the current channel. Prefix: ,unlock",
        inline=False
    )

    embed.add_field(
        name="/purge <amount>",
        value="Clear up to 100 messages",
        inline=False
    )

    embed.add_field(
        name="/role <add/remove> <member> <role>",
        value="Manage roles quickly",
        inline=False
    )

    embed.add_field(
        name="/set_prefix <prefix>",
        value="Change text prefix",
        inline=False
    )

    embed.add_field(
        name="/dmall <message>",
        value="Broadcast DMs to server members",
        inline=False
    )

    await ctx.send(
        embed=embed
    )


# =========================================================
# START BOT
# =========================================================

keep_alive()

BOT_TOKEN = os.getenv("DISCORD_TOKEN")

if BOT_TOKEN:
    bot.run(BOT_TOKEN)
else:
    print(
        "❌ Error: DISCORD_TOKEN environment variable not found!"
    )
