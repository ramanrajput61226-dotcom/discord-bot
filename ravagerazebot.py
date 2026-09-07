import os
import random
import asyncio
import json
import sqlite3
import re
import shutil
import zipfile
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands, tasks
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
BACKUP_DIR = Path("backups")
BACKUP_LIMIT = 10
backup_task_started = False
raid_join_tracking = defaultdict(list)

DB_FILE = "agni_data.sqlite3"
DB = sqlite3.connect(DB_FILE, check_same_thread=False)
DB.row_factory = sqlite3.Row
DB.execute("PRAGMA journal_mode=WAL")
DB.execute("PRAGMA foreign_keys=ON")
DB.execute("""CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id INTEGER PRIMARY KEY,
    log_channel_id INTEGER,
    automod_enabled INTEGER DEFAULT 0,
    bad_words TEXT DEFAULT '',
    link_filter INTEGER DEFAULT 0,
    caps_filter INTEGER DEFAULT 0,
    mention_limit INTEGER DEFAULT 5,
    automod_action TEXT DEFAULT 'delete',
    autorole_id INTEGER,
    goodbye_channel_id INTEGER,
    goodbye_message TEXT,
    verification_channel_id INTEGER,
    verification_role_id INTEGER,
    lockdown INTEGER DEFAULT 0,
    suggestion_channel_id INTEGER,
    poll_channel_id INTEGER,
    reaction_roles TEXT DEFAULT '{}'
)""")
DB.execute("""CREATE TABLE IF NOT EXISTS user_stats (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    messages INTEGER DEFAULT 0,
    xp INTEGER DEFAULT 0,
    level INTEGER DEFAULT 0,
    coins INTEGER DEFAULT 0,
    voice_seconds INTEGER DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
)""")
DB.execute("""CREATE TABLE IF NOT EXISTS invite_stats (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    total INTEGER DEFAULT 0,
    valid INTEGER DEFAULT 0,
    fake INTEGER DEFAULT 0,
    left_count INTEGER DEFAULT 0,
    rejoins INTEGER DEFAULT 0,
    active INTEGER DEFAULT 0,
    coins_awarded INTEGER DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
)""")
DB.execute("""CREATE TABLE IF NOT EXISTS invite_members (
    guild_id INTEGER NOT NULL,
    member_id INTEGER NOT NULL,
    inviter_id INTEGER,
    joined_at TEXT,
    left_at TEXT,
    join_count INTEGER DEFAULT 1,
    PRIMARY KEY (guild_id, member_id)
)""")
DB.execute("""CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
)""")
DB.execute("""CREATE TABLE IF NOT EXISTS mod_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
)""")
DB.commit()
try:
    DB.execute("CREATE TABLE IF NOT EXISTS role_panels (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, message_id INTEGER NOT NULL, role_id INTEGER NOT NULL)")
    DB.commit()
except Exception: pass
try:
    DB.execute("""CREATE TABLE IF NOT EXISTS giveaways (
        message_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
        prize TEXT NOT NULL, winners_count INTEGER NOT NULL, host_id INTEGER NOT NULL,
        fixed_winner_id INTEGER, ended INTEGER DEFAULT 0, participants TEXT DEFAULT '[]',
        winners TEXT DEFAULT '[]', created_at TEXT NOT NULL, ended_at TEXT
    )""")
    DB.commit()
except Exception as e: print(f"[GIVEAWAY DB ERROR] {e}")
for _sql in (
    "ALTER TABLE guild_settings ADD COLUMN welcome_enabled INTEGER DEFAULT 0",
    "ALTER TABLE guild_settings ADD COLUMN welcome_message TEXT",
    "ALTER TABLE guild_settings ADD COLUMN welcome_image TEXT",
    "ALTER TABLE guild_settings ADD COLUMN invite_log_channel_id INTEGER",
    "ALTER TABLE guild_settings ADD COLUMN automod_spam INTEGER DEFAULT 0",
    "ALTER TABLE guild_settings ADD COLUMN automod_duplicate INTEGER DEFAULT 0",
    "ALTER TABLE guild_settings ADD COLUMN automod_invites INTEGER DEFAULT 0",
    "ALTER TABLE guild_settings ADD COLUMN automod_max_message INTEGER DEFAULT 0",
    "ALTER TABLE guild_settings ADD COLUMN raid_enabled INTEGER DEFAULT 0",
    "ALTER TABLE guild_settings ADD COLUMN raid_threshold INTEGER DEFAULT 5",
    "ALTER TABLE guild_settings ADD COLUMN raid_window INTEGER DEFAULT 10",
    "ALTER TABLE guild_settings ADD COLUMN raid_action TEXT DEFAULT 'lockdown'",
):
    try:
        DB.execute(_sql); DB.commit()
    except sqlite3.OperationalError:
        pass

message_cache = defaultdict(lambda: [0, 0])
voice_join_times = {}
afk_users = {}

def db_exec(sql, params=(), commit=True):
    cur = DB.execute(sql, params)
    if commit:
        DB.commit()
    return cur

def ensure_guild(guild_id):
    db_exec("INSERT OR IGNORE INTO guild_settings(guild_id) VALUES (?)", (guild_id,))

def get_guild_setting(guild_id, key, default=None):
    ensure_guild(guild_id)
    row = DB.execute(f"SELECT {key} FROM guild_settings WHERE guild_id=?", (guild_id,)).fetchone()
    if not row or row[key] is None:
        return default
    return row[key]

def set_guild_setting(guild_id, key, value):
    ensure_guild(guild_id)
    DB.execute(f"UPDATE guild_settings SET {key}=? WHERE guild_id=?", (value, guild_id))
    DB.commit()

def ensure_user(guild_id, user_id):
    db_exec("INSERT OR IGNORE INTO user_stats(guild_id,user_id) VALUES (?,?)", (guild_id,user_id))

def add_case(guild_id,user_id,moderator_id,action,reason):
    cur=db_exec("INSERT INTO mod_cases(guild_id,user_id,moderator_id,action,reason,created_at) VALUES (?,?,?,?,?,?)",(guild_id,user_id,moderator_id,action,reason,datetime.now(timezone.utc).isoformat()))
    return cur.lastrowid

def fmt_seconds(seconds):
    seconds=int(seconds or 0); d,seconds=divmod(seconds,86400); h,seconds=divmod(seconds,3600); m,s=divmod(seconds,60)
    return f"{d}d {h}h {m}m {s}s" if d else f"{h}h {m}m {s}s"

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
            bot.add_view(VerifyView())
            for rr in DB.execute("SELECT role_id FROM role_panels").fetchall():
                bot.add_view(RolePanelView(int(rr["role_id"])))
            _persistent_views_loaded = True
        except Exception as e:
            print(f"[PERSISTENT VIEW ERROR] {e}")

    for guild in bot.guilds:
        try:
            invites_cache[guild.id] = await guild.invites()
            print(f" [Invite Cache] Cached invites for guild: {guild.name}")
        except Exception as e:
            print(f" [Invite Cache] Could not cache invites for {guild.name}: {e}")

    global backup_task_started
    if not backup_task_started:
        backup_task_started = True
        await asyncio.to_thread(create_backup)
        if not hourly_backup_task.is_running():
            hourly_backup_task.start()

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


    # Persistent message statistics + economy: 1 coin per message.
    if guild:
        ensure_guild(guild.id)
        ensure_user(guild.id, user_id)
        now = datetime.now(timezone.utc)
        DB.execute("UPDATE user_stats SET messages=messages+1, xp=xp+1, level=(xp+1)/100, coins=coins+1 WHERE guild_id=? AND user_id=?", (guild.id, user_id))
        DB.commit()

        # AFK handling.
        if user_id in afk_users:
            afk_users.pop(user_id, None)
            try:
                await message.channel.send(f"👋 Welcome back, {message.author.mention}! Your AFK status has been removed.", delete_after=5)
            except Exception: pass
        for mentioned in message.mentions:
            if mentioned.id in afk_users and not mentioned.bot:
                reason = afk_users[mentioned.id]
                try:
                    await message.channel.send(f"💤 {mentioned.display_name} is currently AFK: {reason}", delete_after=8)
                except Exception: pass

        # Lightweight configurable AutoMod.
        if not is_whitelisted(message.author, guild) and get_guild_setting(guild.id, "automod_enabled", 0):
            content_lower = message.content.lower()
            bad_words = [w.strip().lower() for w in (get_guild_setting(guild.id,"bad_words","") or "").split(",") if w.strip()]
            link_hit = bool(get_guild_setting(guild.id,"link_filter",0)) and bool(re.search(r"https?://|discord\\.gg/", content_lower))
            caps_hit = bool(get_guild_setting(guild.id,"caps_filter",0)) and len(message.content) >= 12 and sum(c.isupper() for c in message.content if c.isalpha()) / max(1,sum(c.isalpha() for c in message.content)) >= 0.75
            mention_hit = len(message.mentions) >= int(get_guild_setting(guild.id,"mention_limit",5) or 5)
            bad_hit = any(w in content_lower for w in bad_words)
            invite_hit = bool(get_guild_setting(guild.id,"automod_invites",0)) and bool(re.search(r"discord\.gg/|discord(?:app)?\.com/invite/", content_lower))
            max_len = int(get_guild_setting(guild.id,"automod_max_message",0) or 0)
            length_hit = max_len > 0 and len(message.content) > max_len
            spam_enabled = bool(get_guild_setting(guild.id,"automod_spam",0))
            duplicate_enabled = bool(get_guild_setting(guild.id,"automod_duplicate",0))
            recent_messages = getattr(on_message, "_recent", {})
            setattr(on_message, "_recent", recent_messages)
            key=(guild.id,user_id)
            now_ts=datetime.now(timezone.utc).timestamp()
            history=recent_messages.setdefault(key,[])
            history[:] = [(t,c) for t,c in history if now_ts-t<10]
            duplicate_hit = duplicate_enabled and any(c==message.content for _,c in history[-3:]) and bool(message.content.strip())
            history.append((now_ts,message.content))
            spam_hit = spam_enabled and sum(1 for t,_ in history if now_ts-t<5) >= max(3,spam_limit)
            if bad_hit or link_hit or caps_hit or mention_hit or invite_hit or length_hit or duplicate_hit or spam_hit:
                action = get_guild_setting(guild.id,"automod_action","delete") or "delete"
                try: await message.delete()
                except Exception: pass
                try:
                    if action == "timeout":
                        await message.author.timeout(timedelta(minutes=5), reason="AutoMod violation")
                    elif action == "warn":
                        db_exec("INSERT INTO warnings(guild_id,user_id,moderator_id,reason,created_at) VALUES (?,?,?,?,?)",(guild.id,user_id,bot.user.id if bot.user else 0,"AutoMod violation",datetime.now(timezone.utc).isoformat()))
                    elif action == "kick":
                        await message.author.kick(reason="AutoMod violation")
                except Exception: pass
                return

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
    await send_log(guild, "🔨 Member Banned", f"**User:** {user.mention} (`{user.id}`)", discord.Color.red())
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
    await send_log(guild, "🗑️ Channel Deleted", f"**Channel:** #{channel.name} (`{channel.id}`)", discord.Color.red())

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
        try:
            if self.questions:
                await interaction.response.send_modal(
                    DynamicTicketModal(
                        self.panel_id,
                        self.button_id,
                        self.label,
                        self.questions
                    )
                )
                return

            await interaction.response.defer(ephemeral=True)
            await create_ticket(
                interaction,
                self.panel_id,
                self.button_id,
                self.label,
                []
            )

        except Exception as e:
            print(f"[TICKET BUTTON ERROR] {e}")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        "❌ Ticket open karte waqt error aa gaya. Please try again.",
                        ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        "❌ Ticket open karte waqt error aa gaya. Please try again.",
                        ephemeral=True
                    )
            except Exception as followup_error:
                print(f"[TICKET BUTTON FOLLOWUP ERROR] {followup_error}")


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
        await interaction.response.send_message("🔒 Ticket is being closed. The transcript will be sent by DM if possible.", ephemeral=True)
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
            log_id = get_guild_setting(guild.id, "log_channel_id")
            if log_id and guild:
                log_chan = guild.get_channel(int(log_id))
                if log_chan:
                    await log_chan.send(content=f"📁 **Closed Ticket:** `#{channel.name}`\n**Closed By:** {closer.mention}\n**Reason:** {close_reason}", file=discord.File(path, filename=f"{channel.name}_transcript.txt"))
        except Exception as e:
            print(f"[TRANSCRIPT LOG ERROR] {e}")
        try:
            os.remove(path)
        except Exception:
            pass
        await asyncio.sleep(3)
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
        try:
            panel = self.panel()
            if not panel or not panel.get("buttons"):
                return await interaction.response.send_message(
                    "❌ Add at least one button first.",
                    ephemeral=True
                )

            # Acknowledge the interaction BEFORE doing channel/network work.
            # This prevents Discord's "Application did not respond" message.
            await interaction.response.defer(ephemeral=True)

            embed = discord.Embed(
                title=panel.get("title", "Support Hub"),
                description=panel.get("desc", ""),
                color=discord.Color(int(panel.get("ticket_color", 3447003)))
            )

            await interaction.channel.send(
                embed=embed,
                view=DynamicTicketButtonView(
                    self.panel_id,
                    panel["buttons"]
                )
            )

            await interaction.followup.send(
                "✅ Ticket panel deployed successfully.",
                ephemeral=True
            )

        except discord.Forbidden:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "❌ Bot does not have permission to send messages in this channel.",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ Bot does not have permission to send messages in this channel.",
                    ephemeral=True
                )
        except Exception as e:
            print(f"[TICKET DEPLOY ERROR] {e}")
            try:
                await interaction.followup.send(
                    "❌ Ticket panel deploy karte waqt error aa gaya.",
                    ephemeral=True
                )
            except Exception:
                pass


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


@bot.hybrid_command(name="edit_ticket", aliases=["et"], description="Open the ticket panel manager")
@permission_check("administrator")
@app_permission_check("administrator")
async def edit_ticket(ctx: commands.Context):
    if not ctx.guild or not ensure_ticket_structure(ctx.guild.id):
        return await ctx.send("❌ No ticket panels are configured. Run `/setup_ticket` first.", ephemeral=True)
    data = ensure_ticket_structure(ctx.guild.id)
    pid = data.get("active_panel")
    await ctx.send(embed=manager_embed(ctx.guild.id, pid), view=TicketManagerView(ctx.guild.id, pid), ephemeral=True)


# =========================================================
# GIVEAWAY
# =========================================================

@bot.hybrid_command(
    name="giveaway",
    description="Start a random-winner giveaway (slash + prefix)."
)
@app_commands.describe(
    prize="The prize being given away",
    duration="Duration format (e.g. 30s, 10m, 2h, 1d)",
    winners_count="Number of random winners to pick"
)
@permission_check("manage_guild")
@app_permission_check("manage_guild")
async def giveaway(
    ctx: commands.Context,
    prize: str,
    duration: str,
    winners_count: int = 1
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
    DB.execute("INSERT OR REPLACE INTO giveaways(message_id,guild_id,channel_id,prize,winners_count,host_id,created_at) VALUES (?,?,?,?,?,?,?)",
               (msg.id, guild.id, channel.id, prize, max(1, winners_count), author.id, datetime.now(timezone.utc).isoformat()))
    DB.commit()

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

    while (
        len(chosen_winners) < winners_count
        and participants
    ):
        winner_id = random.choice(participants)

        participants.remove(winner_id)

        member = guild.get_member(winner_id)

        if member:
            chosen_winners.append(member)

    DB.execute("UPDATE giveaways SET ended=1, participants=?, winners=?, ended_at=? WHERE message_id=?",
               (json.dumps(participants), json.dumps([w.id for w in chosen_winners]), datetime.now(timezone.utc).isoformat(), msg.id))
    DB.commit()

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
# GIVEAWAY MANAGEMENT / PREFIX-ONLY FIXED WINNER
# =========================================================

@bot.command(name="giveawayfixed")
@commands.guild_only()
async def giveawayfixed_cmd(ctx, duration: str, member: discord.Member, winners_count: int = 1, *, prize: str):
    """Prefix-only fixed-winner giveaway. Example: ,giveawayfixed 1h @User 1 Nitro Gift"""
    if not owner_or_permission(ctx, "manage_guild"):
        return await ctx.send("❌ You need **Manage Server** permission.")
    if winners_count < 1 or winners_count > 20:
        return await ctx.send("❌ Winners must be between 1 and 20.")
    seconds = parse_time(duration)
    embed = discord.Embed(
        title="🎉 GIVEAWAY TIME! 🎉",
        description=f"**Prize:** {prize}\n**Winner(s):** `{winners_count}`\n**Hosted by:** {ctx.author.mention}\n\nReact with 🎉 to enter!",
        color=discord.Color.gold(), timestamp=datetime.now(timezone.utc))
    embed.set_footer(text=f"Ends in {duration} • Fixed winner")
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("🎉")
    DB.execute("INSERT OR REPLACE INTO giveaways(message_id,guild_id,channel_id,prize,winners_count,host_id,fixed_winner_id,created_at) VALUES (?,?,?,?,?,?,?,?)",
               (msg.id, ctx.guild.id, ctx.channel.id, prize, winners_count, ctx.author.id, member.id, datetime.now(timezone.utc).isoformat()))
    DB.commit()
    await asyncio.sleep(seconds)
    try: msg = await ctx.channel.fetch_message(msg.id)
    except Exception: return
    reaction = discord.utils.get(msg.reactions, emoji="🎉")
    participants=[]
    if reaction:
        async for u in reaction.users():
            if not u.bot: participants.append(u.id)
    winners=[]
    if member.id in participants:
        winners.append(member); participants.remove(member.id)
    while len(winners)<winners_count and participants:
        uid=random.choice(participants); participants.remove(uid); m=ctx.guild.get_member(uid)
        if m: winners.append(m)
    DB.execute("UPDATE giveaways SET ended=1, participants=?, winners=?, ended_at=? WHERE message_id=?",
               (json.dumps(participants), json.dumps([w.id for w in winners]), datetime.now(timezone.utc).isoformat(), msg.id)); DB.commit()
    if winners:
        mentions=", ".join(w.mention for w in winners)
        await msg.edit(embed=discord.Embed(title="🎉 GIVEAWAY ENDED 🎉",description=f"**Prize:** {prize}\n**Winner(s):** {mentions} 🏆\n**Winner:** {member.mention}",color=discord.Color.green()),view=None)
        await ctx.send(f"🎊 Congratulations {mentions}! You won **{prize}**!")
    else:
        await msg.edit(embed=discord.Embed(title="🎉 GIVEAWAY ENDED 🎉",description=f"**Prize:** {prize}\n❌ No valid participants found.",color=discord.Color.red()),view=None)

@bot.hybrid_command(name="reroll", description="Reroll an ended giveaway using its message ID.")
@app_commands.describe(message_id="The ended giveaway message ID")
@permission_check("manage_guild")
@app_permission_check("manage_guild")
async def reroll_cmd(ctx, message_id: str):
    try: mid=int(message_id)
    except ValueError: return await send_mod_result(ctx,"❌ Invalid message ID.",True)
    row=DB.execute("SELECT * FROM giveaways WHERE message_id=? AND guild_id=?",(mid,ctx.guild.id)).fetchone()
    if not row or not row["ended"]: return await send_mod_result(ctx,"❌ Ended giveaway not found.",True)
    try: channel=ctx.guild.get_channel(int(row["channel_id"])); msg=await channel.fetch_message(mid)
    except Exception: return await send_mod_result(ctx,"❌ I could not access the giveaway message.",True)
    reaction=discord.utils.get(msg.reactions,emoji="🎉")
    participants=[]
    if reaction:
        async for u in reaction.users():
            if not u.bot: participants.append(u.id)
    old_winners=set(json.loads(row["winners"] or "[]"))
    candidates=[uid for uid in participants if uid not in old_winners]
    if not candidates: return await send_mod_result(ctx,"❌ No eligible participant remains for a reroll.",True)
    uid=random.choice(candidates); member=ctx.guild.get_member(uid) or await bot.fetch_user(uid)
    await ctx.send(f"🎉 **Giveaway Rerolled!** New winner: {member.mention if hasattr(member,'mention') else member} — **{row['prize']}**")
    DB.execute("UPDATE giveaways SET winners=? WHERE message_id=?",(json.dumps(list(old_winners|{uid})),mid)); DB.commit()

# =========================================================
# RAID PROTECTION
# =========================================================

async def handle_raid_join(member):
    guild=member.guild
    if not int(get_guild_setting(guild.id,"raid_enabled",0) or 0): return
    now=datetime.now(timezone.utc).timestamp()
    window=max(3,int(get_guild_setting(guild.id,"raid_window",10) or 10))
    threshold=max(2,int(get_guild_setting(guild.id,"raid_threshold",5) or 5))
    bucket=raid_join_tracking[(guild.id,)]
    bucket[:] = [t for t in bucket if now-t <= window]
    bucket.append(now)
    if len(bucket) < threshold: return
    action=(get_guild_setting(guild.id,"raid_action","lockdown") or "lockdown").lower()
    await send_log(guild,"🚨 Raid Detection Triggered",f"**{len(bucket)} joins** detected within **{window}s**.",discord.Color.red())
    if action in {"lockdown","timeout"}:
        for ch in guild.text_channels:
            try:
                ow=ch.overwrites_for(guild.default_role); ow.send_messages=False
                await ch.set_permissions(guild.default_role,overwrite=ow,reason="Agni Raid Protection")
            except Exception: pass
        set_guild_setting(guild.id,"lockdown",1)
    bucket.clear()

@bot.hybrid_command(name="raidmode", aliases=["rm"], description="Enable or disable raid protection.")
@app_commands.describe(enabled="on or off")
@permission_check("administrator")
@app_permission_check("administrator")
async def raidmode_cmd(ctx, enabled: Literal["on","off"]):
    set_guild_setting(ctx.guild.id,"raid_enabled",1 if enabled=="on" else 0)
    await send_mod_result(ctx,f"🚨 Raid protection **{enabled}**.")

@bot.hybrid_command(name="setraid", aliases=["sr"], description="Configure raid join threshold and detection window.")
@app_commands.describe(joins="Joins required to trigger", seconds="Time window in seconds")
@permission_check("administrator")
@app_permission_check("administrator")
async def setraid_cmd(ctx, joins:int, seconds:int):
    if not 2<=joins<=100 or not 3<=seconds<=120: return await send_mod_result(ctx,"❌ Joins: 2-100; seconds: 3-120.",True)
    set_guild_setting(ctx.guild.id,"raid_threshold",joins); set_guild_setting(ctx.guild.id,"raid_window",seconds)
    await send_mod_result(ctx,f"🚨 Raid threshold set to **{joins} joins / {seconds}s**.")

@bot.hybrid_command(name="raidstatus", aliases=["rs"], description="Show current raid protection settings.")
async def raidstatus_cmd(ctx):
    e=discord.Embed(title="🚨 Raid Protection",color=discord.Color.red())
    e.add_field(name="Enabled",value="Yes" if get_guild_setting(ctx.guild.id,"raid_enabled",0) else "No")
    e.add_field(name="Threshold",value=str(get_guild_setting(ctx.guild.id,"raid_threshold",5)))
    e.add_field(name="Window",value=f"{get_guild_setting(ctx.guild.id,'raid_window',10)}s")
    e.add_field(name="Action",value=str(get_guild_setting(ctx.guild.id,"raid_action","lockdown")))
    await ctx.send(embed=e)

@bot.hybrid_command(name="raidaction", aliases=["ra"], description="Set raid response action: lockdown or log.")
@permission_check("administrator")
@app_permission_check("administrator")
async def raidaction_cmd(ctx, action: Literal["lockdown","log"]):
    set_guild_setting(ctx.guild.id,"raid_action",action); await send_mod_result(ctx,f"🚨 Raid action set to **{action}**.")

# =========================================================
# FULLER AUTOMOD CONFIGURATION
# =========================================================

@bot.hybrid_command(name="automod_reset", aliases=["ar"], description="Reset AutoMod settings to safe defaults.")
@permission_check("administrator")
@app_permission_check("administrator")
async def automod_reset_cmd(ctx):
    defaults={"automod_enabled":0,"bad_words":"","link_filter":0,"caps_filter":0,"mention_limit":5,"automod_action":"delete","automod_spam":0,"automod_duplicate":0,"automod_invites":0,"automod_max_message":0}
    for k,v in defaults.items(): set_guild_setting(ctx.guild.id,k,v)
    await send_mod_result(ctx,"🤖 AutoMod settings reset.")

@bot.hybrid_command(name="automod_status", aliases=["as"], description="Show all AutoMod settings.")
async def automod_status_cmd(ctx):
    e=discord.Embed(title="🤖 AutoMod Status",color=discord.Color.orange())
    for label,key,default in [("Enabled","automod_enabled",0),("Bad Words","bad_words","None"),("Links","link_filter",0),("Caps","caps_filter",0),("Mention Limit","mention_limit",5),("Action","automod_action","delete"),("Spam","automod_spam",0),("Duplicate Messages","automod_duplicate",0),("Discord Invites","automod_invites",0),("Max Message Length","automod_max_message",0)]:
        val=get_guild_setting(ctx.guild.id,key,default); val="On" if key.startswith("automod_") and key not in {"automod_action","automod_max_message"} and val else ("On" if key in {"link_filter","caps_filter"} and val else val)
        e.add_field(name=label,value=str(val)[:1024],inline=True)
    await ctx.send(embed=e)

# =========================================================
# MESSAGE COUNT COMMANDS
# =========================================================

@bot.hybrid_command(name="messagecount", aliases=["msgc"], description="Show message count for a member.")
@app_commands.describe(member="Member")
async def messagecount_cmd(ctx, member: discord.Member=None):
    member=member or ctx.author; ensure_user(ctx.guild.id,member.id)
    r=DB.execute("SELECT messages FROM user_stats WHERE guild_id=? AND user_id=?",(ctx.guild.id,member.id)).fetchone()
    await ctx.send(f"💬 **{member.display_name}** has sent **{r['messages']:,}** messages in **{ctx.guild.name}**.")

@bot.hybrid_command(name="messageleaderboard", aliases=["mlb"], description="Show the server message leaderboard.")
async def messageleaderboard_cmd(ctx):
    rows=DB.execute("SELECT user_id,messages FROM user_stats WHERE guild_id=? ORDER BY messages DESC LIMIT 10",(ctx.guild.id,)).fetchall()
    lines=[f"**{i}.** <@{r['user_id']}> — `{r['messages']:,}` messages" for i,r in enumerate(rows,1)]
    await ctx.send(embed=discord.Embed(title="💬 Message Leaderboard",description="\n".join(lines) or "No message data yet.",color=discord.Color.blurple()))

# =========================================================
# HOURLY BACKUPS — ALL SERVERS IN ONE DATABASE
# =========================================================

def create_backup():
    BACKUP_DIR.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    db_copy=BACKUP_DIR/f"agni_data_{stamp}.sqlite3"
    zip_path=BACKUP_DIR/f"agni_backup_{stamp}.zip"
    try:
        src=sqlite3.connect(DB_FILE)
        dst=sqlite3.connect(db_copy)
        src.backup(dst); dst.close(); src.close()
        with zipfile.ZipFile(zip_path,"w",zipfile.ZIP_DEFLATED) as z:
            z.write(db_copy,db_copy.name)
            if Path(CONFIG_FILE).exists(): z.write(CONFIG_FILE,CONFIG_FILE)
        db_copy.unlink(missing_ok=True)
        backups=sorted(BACKUP_DIR.glob("agni_backup_*.zip"),key=lambda x:x.stat().st_mtime,reverse=True)
        for old in backups[BACKUP_LIMIT:]: old.unlink(missing_ok=True)
        print(f"[BACKUP] Created {zip_path}")
        return zip_path
    except Exception as e:
        print(f"[BACKUP ERROR] {e}")
        try: db_copy.unlink(missing_ok=True)
        except Exception: pass
        return None

@tasks.loop(hours=1)
async def hourly_backup_task():
    await asyncio.to_thread(create_backup)

@hourly_backup_task.before_loop
async def before_hourly_backup():
    await bot.wait_until_ready()

@bot.hybrid_command(name="backup", description="Send the latest Agni backup. Owner only.")
async def backup_cmd(ctx):
    if ctx.author.id != OWNER_ID: return await send_mod_result(ctx,"❌ Owner only.",True)
    backups=sorted(BACKUP_DIR.glob("agni_backup_*.zip"),key=lambda x:x.stat().st_mtime,reverse=True)
    if not backups: return await send_mod_result(ctx,"❌ No backup exists yet.",True)
    await ctx.send(content=f"📦 Latest backup: `{backups[0].name}` — contains all server data stored by Agni.",file=discord.File(backups[0]))

@bot.hybrid_command(name="backuplist", description="List the latest 10 Agni backups. Owner only.")
async def backuplist_cmd(ctx):
    if ctx.author.id != OWNER_ID: return await send_mod_result(ctx,"❌ Owner only.",True)
    backups=sorted(BACKUP_DIR.glob("agni_backup_*.zip"),key=lambda x:x.stat().st_mtime,reverse=True)
    text="\n".join(f"{i}. `{b.name}` — {b.stat().st_size/1024/1024:.2f} MB" for i,b in enumerate(backups[:BACKUP_LIMIT],1)) or "No backups yet."
    await ctx.send(embed=discord.Embed(title="📦 Agni Backups",description=text,color=discord.Color.blue()))

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
    set_guild_setting(ctx.guild.id, "log_channel_id", channel.id)
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
    if ctx.guild and ensure_ticket_structure(ctx.guild.id):
        for panel in ensure_ticket_structure(ctx.guild.id).get("panels", {}).values():
            panel["ticket_open_message"] = message
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

        if not interaction.guild:
            return await interaction.response.send_message("❌ This can only be used in a server.", ephemeral=True)
        custom_welcome_msg = self.wel_msg.value

        custom_welcome_img = (
            self.wel_img.value.strip()
            if self.wel_img.value
            else None
        )

        welcome_enabled = True
        set_guild_setting(interaction.guild.id, "welcome_enabled", 1)
        set_guild_setting(interaction.guild.id, "welcome_message", custom_welcome_msg)
        set_guild_setting(interaction.guild.id, "welcome_image", custom_welcome_img)

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
        set_guild_setting(interaction.guild.id, "welcome_channel_id", selected_channel.id)
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
    set_guild_setting(ctx.guild.id, "welcome_enabled", 0)
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


    # Persistent invite accounting: valid/left/rejoin/active + 100 coins per new invite.
    try:
        ensure_guild(guild.id)
        existing = DB.execute("SELECT * FROM invite_members WHERE guild_id=? AND member_id=?", (guild.id, member.id)).fetchone()
        if inviter_user:
            if existing:
                DB.execute("UPDATE invite_members SET inviter_id=?, joined_at=?, left_at=NULL, join_count=join_count+1 WHERE guild_id=? AND member_id=?", (inviter_user.id, datetime.now(timezone.utc).isoformat(), guild.id, member.id))
                DB.execute("INSERT OR IGNORE INTO invite_stats(guild_id,user_id) VALUES (?,?)", (guild.id, inviter_user.id))
                DB.execute("UPDATE invite_stats SET rejoins=rejoins+1, active=active+1 WHERE guild_id=? AND user_id=?", (guild.id, inviter_user.id))
            else:
                DB.execute("INSERT OR REPLACE INTO invite_members(guild_id,member_id,inviter_id,joined_at,left_at,join_count) VALUES (?,?,?,?,NULL,1)", (guild.id, member.id, inviter_user.id, datetime.now(timezone.utc).isoformat()))
                DB.execute("INSERT OR IGNORE INTO invite_stats(guild_id,user_id) VALUES (?,?)", (guild.id, inviter_user.id))
                age_days=(datetime.now(timezone.utc)-member.created_at).total_seconds()/86400
                if age_days < 7:
                    DB.execute("UPDATE invite_stats SET total=total+1, fake=fake+1 WHERE guild_id=? AND user_id=?", (guild.id, inviter_user.id))
                else:
                    DB.execute("UPDATE invite_stats SET total=total+1, valid=valid+1, active=active+1, coins_awarded=coins_awarded+100 WHERE guild_id=? AND user_id=?", (guild.id, inviter_user.id))
                    ensure_user(guild.id, inviter_user.id)
                    DB.execute("UPDATE user_stats SET coins=coins+100 WHERE guild_id=? AND user_id=?", (guild.id, inviter_user.id))
            DB.commit()
    except Exception as e:
        print(f"[INVITE DB ERROR] {e}")

    global invite_log_channel_id

    target = channel or ctx.channel

    invite_log_channel_id = target.id
    set_guild_setting(ctx.guild.id, "invite_log_channel_id", target.id)
    save_persistent_settings()

    await ctx.send(
        f"✅ **Invite Logger set to:** "
        f"{target.mention}"
    )


@bot.hybrid_command(
    name="invites",
    description="Check detailed invite statistics of a server member."
)
async def invites(ctx: commands.Context, member: discord.Member = None):
    target = member or ctx.author
    row = DB.execute("SELECT * FROM invite_stats WHERE guild_id=? AND user_id=?", (ctx.guild.id, target.id)).fetchone()
    v = row if row else {"total":0,"valid":0,"fake":0,"left_count":0,"rejoins":0,"active":0,"coins_awarded":0}
    embed = discord.Embed(title=f"Invite Stats: {target.display_name}", color=discord.Color.blue())
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Total Invites", value=f"`{v['total']}`")
    embed.add_field(name="Valid", value=f"`{v['valid']}`")
    embed.add_field(name="Fake", value=f"`{v['fake']}`")
    embed.add_field(name="Left", value=f"`{v['left_count']}`")
    embed.add_field(name="Rejoins", value=f"`{v['rejoins']}`")
    embed.add_field(name="Active", value=f"`{v['active']}`")
    embed.add_field(name="Invite Coins", value=f"`{v['coins_awarded']:,}`", inline=False)
    await ctx.send(embed=embed)


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
    await handle_raid_join(member)

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

    await send_log(guild, "📥 Member Joined", f"**Member:** {member.mention} (`{member.id}`)", discord.Color.green())

    global invite_log_channel_id

    db_invite_log_id = get_guild_setting(guild.id, "invite_log_channel_id")
    if db_invite_log_id or invite_log_channel_id:

        log_channel = guild.get_channel(int(db_invite_log_id or invite_log_channel_id))

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

    await on_member_join_autorole(member)

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

        db_welcome_img = get_guild_setting(guild.id, "welcome_image")
        if db_welcome_img or custom_welcome_img:
            embed.set_image(url=db_welcome_img or custom_welcome_img)

        embed.set_footer(
            text=f"Member #{guild.member_count}"
        )

        await target_channel.send(
            content=f"Welcome {member.mention}!",
            embed=embed
        )



# =========================================================
# ALL-ROUNDER EXPANSION
# =========================================================

async def send_mod_result(ctx, text, ephemeral=False):
    try: return await ctx.send(text, ephemeral=ephemeral)
    except TypeError: return await ctx.send(text)

@bot.event
async def on_member_remove(member):
    guild=member.guild
    await send_log(guild, "📤 Member Left", f"**Member:** {member.mention} (`{member.id}`)", discord.Color.red())
    try:
        row=DB.execute("SELECT inviter_id FROM invite_members WHERE guild_id=? AND member_id=?",(guild.id,member.id)).fetchone()
        if row and row["inviter_id"]:
            DB.execute("UPDATE invite_stats SET left_count=left_count+1, active=CASE WHEN active>0 THEN active-1 ELSE 0 END WHERE guild_id=? AND user_id=?",(guild.id,row["inviter_id"]))
            DB.execute("UPDATE invite_members SET left_at=? WHERE guild_id=? AND member_id=?",(datetime.now(timezone.utc).isoformat(),guild.id,member.id)); DB.commit()
        goodbye_channel_id=get_guild_setting(guild.id,"goodbye_channel_id")
        goodbye_message=get_guild_setting(guild.id,"goodbye_message")
        if goodbye_channel_id and goodbye_message:
            ch=guild.get_channel(int(goodbye_channel_id))
            if ch:
                await ch.send(goodbye_message.replace("{user}",member.mention).replace("{username}",member.display_name).replace("{server}",guild.name).replace("{count}",str(guild.member_count or 0)))
    except Exception as e: print(f"[MEMBER REMOVE ERROR] {e}")

@bot.hybrid_command(name="kick", description="Kick a member from the server.")
@app_commands.describe(member="Member to kick", reason="Reason")
@permission_check("kick_members")
@app_permission_check("kick_members")
async def kick_cmd(ctx, member: discord.Member, reason: str="No reason provided"):
    h=_hierarchy_error(ctx,member)
    if h: return await send_mod_result(ctx,h)
    try: await member.kick(reason=reason); case=add_case(ctx.guild.id,member.id,ctx.author.id,"KICK",reason); await send_mod_result(ctx,f"👢 {member.mention} was kicked. Case #{case}.")
    except discord.Forbidden: await send_mod_result(ctx,"❌ I don't have permission to kick that member.",True)

@bot.hybrid_command(name="unban", description="Unban a user by ID.")
@app_commands.describe(user_id="User ID", reason="Reason")
@permission_check("ban_members")
@app_permission_check("ban_members")
async def unban_cmd(ctx,user_id:str,reason:str="No reason provided"):
    try:
        user=await bot.fetch_user(int(user_id)); await ctx.guild.unban(user,reason=reason); case=add_case(ctx.guild.id,user.id,ctx.author.id,"UNBAN",reason); await send_mod_result(ctx,f"🔓 {user} was unbanned. Case #{case}.")
    except (ValueError,discord.NotFound): await send_mod_result(ctx,"❌ User ID is invalid or the user is not banned.",True)
    except discord.Forbidden: await send_mod_result(ctx,"❌ I don't have permission to unban users.",True)

@bot.hybrid_command(name="softban", description="Ban and immediately unban a member.")
@app_commands.describe(member="Member", reason="Reason")
@permission_check("ban_members")
@app_permission_check("ban_members")
async def softban_cmd(ctx,member:discord.Member,reason:str="No reason provided"):
    h=_hierarchy_error(ctx,member)
    if h:return await send_mod_result(ctx,h)
    try:
        await member.ban(reason=reason,delete_message_seconds=86400); await ctx.guild.unban(member,reason="Softban completed"); case=add_case(ctx.guild.id,member.id,ctx.author.id,"SOFTBAN",reason); await send_mod_result(ctx,f"🔨 {member.mention} was softbanned. Case #{case}.")
    except discord.Forbidden: await send_mod_result(ctx,"❌ I don't have permission to softban that member.",True)

@bot.hybrid_command(name="warn", description="Warn a member and save the warning.")
@app_commands.describe(member="Member", reason="Warning reason")
@permission_check("moderate_members")
@app_permission_check("moderate_members")
async def warn_cmd(ctx,member:discord.Member,reason:str):
    cid=add_case(ctx.guild.id,member.id,ctx.author.id,"WARN",reason)
    db_exec("INSERT INTO warnings(guild_id,user_id,moderator_id,reason,created_at) VALUES (?,?,?,?,?)",(ctx.guild.id,member.id,ctx.author.id,reason,datetime.now(timezone.utc).isoformat()))
    try: await member.send(f"⚠️ You were warned in **{ctx.guild.name}**. Reason: {reason}")
    except Exception: pass
    await send_mod_result(ctx,f"⚠️ {member.mention} has been warned. Case #{cid}.")

@bot.hybrid_command(name="warnings", description="Show a member's warnings.")
@app_commands.describe(member="Member")
async def warnings_cmd(ctx,member:discord.Member=None):
    member=member or ctx.author; rows=DB.execute("SELECT id,reason,moderator_id,created_at FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 25",(ctx.guild.id,member.id)).fetchall()
    text="\n".join(f"#{r['id']} — <@{r['moderator_id']}> — {r['reason']}" for r in rows) or "No warnings."
    embed=discord.Embed(title=f"Warnings — {member}",description=text[:4096],color=discord.Color.orange()); await ctx.send(embed=embed)

@bot.hybrid_command(name="clearwarnings", description="Clear all warnings for a member.")
@app_commands.describe(member="Member")
@permission_check("moderate_members")
@app_permission_check("moderate_members")
async def clearwarnings_cmd(ctx,member:discord.Member):
    db_exec("DELETE FROM warnings WHERE guild_id=? AND user_id=?",(ctx.guild.id,member.id)); await send_mod_result(ctx,f"🧹 Warnings cleared for {member.mention}.")

@bot.hybrid_command(name="history", description="Show recent moderation cases for a member.")
@app_commands.describe(member="Member")
async def history_cmd(ctx,member:discord.Member=None):
    member=member or ctx.author; rows=DB.execute("SELECT id,action,reason,moderator_id FROM mod_cases WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 20",(ctx.guild.id,member.id)).fetchall()
    text="\n".join(f"Case #{r['id']} • **{r['action']}** • <@{r['moderator_id']}> • {r['reason']}" for r in rows) or "No moderation history."
    await ctx.send(embed=discord.Embed(title=f"Moderation History — {member}",description=text[:4096],color=discord.Color.blue()))

@bot.hybrid_command(name="slowmode", description="Set the current channel slowmode in seconds.")
@app_commands.describe(seconds="0-21600 seconds")
@permission_check("manage_channels")
@app_permission_check("manage_channels")
async def slowmode_cmd(ctx,seconds:int):
    if seconds<0 or seconds>21600:return await send_mod_result(ctx,"❌ Seconds must be between 0 and 21600.",True)
    try: await ctx.channel.edit(slowmode_delay=seconds); await send_mod_result(ctx,f"🐢 Slowmode set to **{seconds}s**.")
    except discord.Forbidden: await send_mod_result(ctx,"❌ I need Manage Channels permission.",True)

async def _voice_action(ctx,member,action):
    h=_hierarchy_error(ctx,member)
    if h:return await send_mod_result(ctx,h)
    if not member.voice or not member.voice.channel:return await send_mod_result(ctx,"❌ That member is not in a voice channel.",True)
    try:
        if action=="deafen": await member.edit(deafen=True)
        elif action=="undeafen": await member.edit(deafen=False)
        elif action=="mute": await member.edit(mute=True)
        elif action=="unmute": await member.edit(mute=False)
        await send_mod_result(ctx,f"🎙️ Voice action **{action}** applied to {member.mention}.")
    except discord.Forbidden: await send_mod_result(ctx,"❌ I need the required voice permissions.",True)

@bot.hybrid_command(name="deafen",description="Server-deafen a member.")
@app_commands.describe(member="Member")
@permission_check("deafen_members")
@app_permission_check("deafen_members")
async def deafen_cmd(ctx,member:discord.Member): await _voice_action(ctx,member,"deafen")

@bot.hybrid_command(name="undeafen",description="Remove server-deafen from a member.")
@app_commands.describe(member="Member")
@permission_check("deafen_members")
@app_permission_check("deafen_members")
async def undeafen_cmd(ctx,member:discord.Member): await _voice_action(ctx,member,"undeafen")

@bot.hybrid_command(name="voicemute",description="Server-mute a member in voice.")
@app_commands.describe(member="Member")
@permission_check("mute_members")
@app_permission_check("mute_members")
async def voicemute_cmd(ctx,member:discord.Member): await _voice_action(ctx,member,"mute")

@bot.hybrid_command(name="voiceunmute",description="Remove server-mute from a member.")
@app_commands.describe(member="Member")
@permission_check("mute_members")
@app_permission_check("mute_members")
async def voiceunmute_cmd(ctx,member:discord.Member): await _voice_action(ctx,member,"unmute")

@bot.hybrid_command(name="move",description="Move a member to your current voice channel.")
@app_commands.describe(member="Member")
@permission_check("move_members")
@app_permission_check("move_members")
async def move_cmd(ctx,member:discord.Member):
    if not ctx.author.voice or not ctx.author.voice.channel:return await send_mod_result(ctx,"❌ You must be in a voice channel.",True)
    try: await member.move_to(ctx.author.voice.channel); await send_mod_result(ctx,f"🔀 Moved {member.mention} to {ctx.author.voice.channel.mention}.")
    except discord.Forbidden: await send_mod_result(ctx,"❌ I need Move Members permission.",True)

@bot.hybrid_command(name="nick",description="Change a member's nickname.")
@app_commands.describe(member="Member", nickname="New nickname; leave blank to reset")
@permission_check("manage_nicknames")
@app_permission_check("manage_nicknames")
async def nick_cmd(ctx,member:discord.Member,nickname:str=None):
    h=_hierarchy_error(ctx,member)
    if h:return await send_mod_result(ctx,h)
    try: await member.edit(nick=nickname); await send_mod_result(ctx,f"✏️ Nickname updated for {member.mention}.")
    except discord.Forbidden: await send_mod_result(ctx,"❌ I cannot change that member's nickname.",True)

# Logging
@bot.hybrid_command(name="setlog",description="Set the server moderation/event log channel.")
@permission_check("administrator")
@app_permission_check("administrator")
async def setlog_cmd(ctx,channel:discord.TextChannel):
    set_guild_setting(ctx.guild.id,"log_channel_id",channel.id); await send_mod_result(ctx,f"📜 Log channel set to {channel.mention}.")

async def send_log(guild,title,description,color=discord.Color.blurple()):
    cid=get_guild_setting(guild.id,"log_channel_id")
    if not cid:return
    ch=guild.get_channel(int(cid))
    if ch:
        try: await ch.send(embed=discord.Embed(title=title,description=description,color=color,timestamp=datetime.now(timezone.utc)))
        except Exception: pass

@bot.event
async def on_message_delete(message):
    if message.guild and not message.author.bot: await send_log(message.guild,"🗑️ Message Deleted",f"**Author:** {message.author.mention}\n**Channel:** {message.channel.mention}\n**Content:** {(message.content or '[no text]')[:1500]}",discord.Color.red())

@bot.event
async def on_message_edit(before,after):
    if before.guild and not before.author.bot and before.content!=after.content: await send_log(before.guild,"✏️ Message Edited",f"**Author:** {before.author.mention}\n**Channel:** {before.channel.mention}\n**Before:** {(before.content or '[no text]')[:700]}\n**After:** {(after.content or '[no text]')[:700]}",discord.Color.orange())

async def on_member_ban_logger(guild,user):
    await send_log(guild,"🔨 Member Banned",f"**User:** {user.mention} (`{user.id}`)",discord.Color.red())
    # Preserve existing anti-nuke behavior under a separate audit pass.
    try:
        async for entry in guild.audit_logs(limit=1,action=discord.AuditLogAction.ban):
            executor=entry.user
            if executor.bot or is_whitelisted(executor,guild): return
            now=datetime.now().timestamp(); data=nuke_tracking.setdefault((guild.id,executor.id),{"bans":[],"channels":[]}); data["bans"]=[t for t in data["bans"] if now-t<120]; data["bans"].append(now)
            if len(data["bans"])>=ban_limit: await guild.ban(executor,reason="Anti-Nuke Triggered: Mass banning members.")
            break
    except Exception as e: print(f"[ANTI-NUKE BAN] {e}")

@bot.event
async def on_guild_channel_create(channel):
    await send_log(channel.guild,"📁 Channel Created",f"{channel.mention} (`{channel.id}`)",discord.Color.green())

async def on_guild_channel_delete_log(channel):
    await send_log(channel.guild,"🗑️ Channel Deleted",f"**Channel:** #{channel.name} (`{channel.id}`)",discord.Color.red())

# AutoMod configuration
@bot.hybrid_command(name="automod",aliases=["am"],description="Configure AutoMod: on/off, words, links, caps, mentions, action.")
@permission_check("administrator")
@app_permission_check("administrator")
async def automod_cmd(ctx,setting:str,value:str):
    setting=setting.lower(); value=value.strip()
    allowed={"enabled":"automod_enabled","words":"bad_words","links":"link_filter","caps":"caps_filter","mentions":"mention_limit","action":"automod_action","spam":"automod_spam","duplicates":"automod_duplicate","invites":"automod_invites","maxlength":"automod_max_message"}
    if setting not in allowed:return await send_mod_result(ctx,"❌ Settings: enabled, words, links, caps, mentions, action, spam, duplicates, invites, maxlength",True)
    key=allowed[setting]
    if key in {"automod_enabled","link_filter","caps_filter","automod_spam","automod_duplicate","automod_invites"}:
        if value.lower() not in {"on","off","true","false","1","0"}: return await send_mod_result(ctx,"❌ Use on/off.",True)
        value=1 if value.lower() in {"on","true","1"} else 0
    elif key=="mention_limit":
        try:value=max(1,min(50,int(value)))
        except:return await send_mod_result(ctx,"❌ Mention limit must be a number.",True)
    elif key=="automod_action" and value not in {"delete","warn","timeout","kick"}:return await send_mod_result(ctx,"❌ Action: delete, warn, timeout, kick",True)
    elif key=="automod_max_message":
        try: value=max(0,min(4000,int(value)))
        except: return await send_mod_result(ctx,"❌ maxlength must be a number (0 disables it).",True)
    set_guild_setting(ctx.guild.id,key,value); await send_mod_result(ctx,f"🤖 AutoMod **{setting}** updated.")

@bot.hybrid_command(name="afk",description="Set or remove your AFK status.")
@app_commands.describe(reason="AFK reason")
async def afk_cmd(ctx,reason:str="AFK"):
    afk_users[ctx.author.id]=reason; await send_mod_result(ctx,f"💤 {ctx.author.mention} is now AFK: {reason}")

# Message statistics / XP
@bot.hybrid_command(name="stats",aliases=["st"],description="Show message, XP and economy statistics.")
@app_commands.describe(member="Member")
async def stats_cmd(ctx,member:discord.Member=None):
    member=member or ctx.author; ensure_user(ctx.guild.id,member.id); r=DB.execute("SELECT * FROM user_stats WHERE guild_id=? AND user_id=?",(ctx.guild.id,member.id)).fetchone()
    embed=discord.Embed(title=f"📊 Statistics — {member.display_name}",color=discord.Color.blue()); embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Messages",value=f"{r['messages']:,}"); embed.add_field(name="XP",value=f"{r['xp']:,}"); embed.add_field(name="Level",value=str(r['level'])); embed.add_field(name="Balance",value=f"{r['coins']:,} coins"); embed.add_field(name="Voice Time",value=fmt_seconds(r['voice_seconds']),inline=False); await ctx.send(embed=embed)

@bot.hybrid_command(name="leaderboard",aliases=["lb"],description="Show a server leaderboard.")
@app_commands.describe(category="messages, coins, xp or voice")
async def leaderboard_cmd(ctx,category:str="messages"):
    category=category.lower(); col={"messages":"messages","coins":"coins","xp":"xp","voice":"voice_seconds"}.get(category)
    if not col:return await send_mod_result(ctx,"❌ Category: messages, coins, xp, voice",True)
    rows=DB.execute(f"SELECT user_id,{col} FROM user_stats WHERE guild_id=? ORDER BY {col} DESC LIMIT 10",(ctx.guild.id,)).fetchall()
    lines=[]
    for i,r in enumerate(rows,1):
        m=ctx.guild.get_member(r['user_id']); name=m.mention if m else f"<@{r['user_id']}>"; val=fmt_seconds(r[col]) if col=="voice_seconds" else f"{r[col]:,}"; lines.append(f"**{i}.** {name} — `{val}`")
    await ctx.send(embed=discord.Embed(title=f"🏆 {category.title()} Leaderboard",description="\n".join(lines) or "No data yet.",color=discord.Color.gold()))

# Economy: no daily command; 1 coin/message and 100 coins/new valid invite.
@bot.hybrid_command(name="balance",aliases=["bal","b"],description="Show your or another member's balance.")
@app_commands.describe(member="Member")
async def balance_cmd(ctx,member:discord.Member=None):
    member=member or ctx.author; ensure_user(ctx.guild.id,member.id); r=DB.execute("SELECT coins FROM user_stats WHERE guild_id=? AND user_id=?",(ctx.guild.id,member.id)).fetchone(); await ctx.send(f"💰 **{member.display_name}** has **{r['coins']:,}** coins.")

@bot.hybrid_command(name="pay",aliases=["p"],description="Pay coins to another member.")
@app_commands.describe(member="Recipient", amount="Amount")
async def pay_cmd(ctx,member:discord.Member,amount:int):
    if member.bot or member.id==ctx.author.id:return await send_mod_result(ctx,"❌ Choose another human member.",True)
    if amount<=0:return await send_mod_result(ctx,"❌ Amount must be positive.",True)
    ensure_user(ctx.guild.id,ctx.author.id); ensure_user(ctx.guild.id,member.id); r=DB.execute("SELECT coins FROM user_stats WHERE guild_id=? AND user_id=?",(ctx.guild.id,ctx.author.id)).fetchone()
    if r['coins']<amount:return await send_mod_result(ctx,"❌ Insufficient balance.",True)
    DB.execute("UPDATE user_stats SET coins=coins-? WHERE guild_id=? AND user_id=?",(amount,ctx.guild.id,ctx.author.id)); DB.execute("UPDATE user_stats SET coins=coins+? WHERE guild_id=? AND user_id=?",(amount,ctx.guild.id,member.id)); DB.commit(); await send_mod_result(ctx,f"💸 {ctx.author.mention} paid **{amount:,}** coins to {member.mention}.")

# Invite statistics
@bot.hybrid_command(name="inviteleaderboard",aliases=["ilb"],description="Show the server invite leaderboard.")
async def inviteleaderboard_cmd(ctx):
    rows=DB.execute("SELECT user_id,total,valid,left_count,rejoins,active FROM invite_stats WHERE guild_id=? ORDER BY valid DESC LIMIT 10",(ctx.guild.id,)).fetchall(); lines=[]
    for i,r in enumerate(rows,1):lines.append(f"**{i}.** <@{r['user_id']}> — `{r['valid']}` valid • `{r['left_count']}` left • `{r['rejoins']}` rejoins • `{r['active']}` active")
    await ctx.send(embed=discord.Embed(title="🏆 Invite Leaderboard",description="\n".join(lines) or "No invite data yet.",color=discord.Color.gold()))

@bot.hybrid_command(name="inviteinfo",aliases=["ii"],description="Show detailed invite statistics for a member.")
@app_commands.describe(member="Member")
async def inviteinfo_cmd(ctx,member:discord.Member=None):
    member=member or ctx.author; r=DB.execute("SELECT * FROM invite_stats WHERE guild_id=? AND user_id=?",(ctx.guild.id,member.id)).fetchone(); vals=r or {"total":0,"valid":0,"fake":0,"left_count":0,"rejoins":0,"active":0,"coins_awarded":0}
    e=discord.Embed(title=f"📊 Invite Stats — {member.display_name}",color=discord.Color.blue()); e.add_field(name="Total",value=str(vals['total'])); e.add_field(name="Valid",value=str(vals['valid'])); e.add_field(name="Fake",value=str(vals['fake'])); e.add_field(name="Left",value=str(vals['left_count'])); e.add_field(name="Rejoins",value=str(vals['rejoins'])); e.add_field(name="Active",value=str(vals['active'])); e.add_field(name="Invite Coins",value=f"{vals['coins_awarded']:,}",inline=False); await ctx.send(embed=e)

@bot.hybrid_command(name="inviter", aliases=["iv"], description="Show who invited a member to this server.")
@app_commands.describe(member="Member whose inviter you want to check")
async def inviter_cmd(ctx, member: discord.Member = None):
    if not ctx.guild:
        return await ctx.send("❌ This command can only be used inside a server.")
    member = member or ctx.author
    row = DB.execute(
        "SELECT inviter_id, join_count, joined_at FROM invite_members WHERE guild_id=? AND member_id=?",
        (ctx.guild.id, member.id)
    ).fetchone()
    if not row or not row["inviter_id"]:
        return await ctx.send(f"ℹ️ No inviter record found for **{member.display_name}**.")
    inviter = ctx.guild.get_member(int(row["inviter_id"]))
    inviter_text = inviter.mention if inviter else f"<@{row['inviter_id']}>"
    embed = discord.Embed(title=f"📨 Inviter — {member.display_name}", color=discord.Color.blurple())
    embed.add_field(name="Invited By", value=inviter_text)
    embed.add_field(name="Join Count", value=str(row["join_count"] or 1))
    if row["joined_at"]:
        try:
            dt = datetime.fromisoformat(row["joined_at"])
            embed.add_field(name="Joined At", value=discord.utils.format_dt(dt, "F"), inline=False)
        except Exception:
            pass
    await ctx.send(embed=embed)


# Goodbye / autorole
@bot.hybrid_command(name="setautorole",aliases=["sar"],description="Set the role automatically given to new members.")
@permission_check("administrator")
@app_permission_check("administrator")
async def setautorole_cmd(ctx,role:discord.Role=None):
    if role and ctx.guild.me and role>=ctx.guild.me.top_role:return await send_mod_result(ctx,"❌ I cannot assign that role.",True)
    set_guild_setting(ctx.guild.id,"autorole_id",role.id if role else None); await send_mod_result(ctx,f"✅ Autorole set to {role.mention}." if role else "✅ Autorole disabled.")

@bot.hybrid_command(name="setgoodbye",aliases=["sg"],description="Configure goodbye channel and message.")
@permission_check("administrator")
@app_permission_check("administrator")
async def setgoodbye_cmd(ctx,channel:discord.TextChannel,message:str):
    set_guild_setting(ctx.guild.id,"goodbye_channel_id",channel.id); set_guild_setting(ctx.guild.id,"goodbye_message",message); await send_mod_result(ctx,"👋 Goodbye system updated.")

# Patch autorole into a listener without replacing the existing welcome listener.
async def on_member_join_autorole(member):
    try:
        rid=get_guild_setting(member.guild.id,"autorole_id")
        if rid:
            role=member.guild.get_role(int(rid))
            if role: await member.add_roles(role,reason="Agni autorole")
    except Exception as e: print(f"[AUTOROLE ERROR] {e}")

# Suggestions
@bot.hybrid_command(name="suggest",description="Submit a server suggestion.")
async def suggest_cmd(ctx,suggestion:str):
    cid=get_guild_setting(ctx.guild.id,"suggestion_channel_id"); ch=ctx.guild.get_channel(int(cid)) if cid else ctx.channel
    e=discord.Embed(title="💡 New Suggestion",description=suggestion,color=discord.Color.blurple()); e.set_author(name=str(ctx.author),icon_url=ctx.author.display_avatar.url); msg=await ch.send(embed=e); await msg.add_reaction("👍"); await msg.add_reaction("👎"); await send_mod_result(ctx,"✅ Suggestion submitted.",True)

@bot.hybrid_command(name="setsuggestions",aliases=["ssug"],description="Set the suggestion channel.")
@permission_check("administrator")
@app_permission_check("administrator")
async def setsuggestions_cmd(ctx,channel:discord.TextChannel): set_guild_setting(ctx.guild.id,"suggestion_channel_id",channel.id); await send_mod_result(ctx,f"💡 Suggestion channel set to {channel.mention}.")

# Polls
@bot.hybrid_command(name="poll",description="Create a simple yes/no poll.")
async def poll_cmd(ctx,question:str):
    e=discord.Embed(title="📊 Poll",description=question,color=discord.Color.blue()); m=await ctx.send(embed=e); await m.add_reaction("👍"); await m.add_reaction("👎")

# Verification
class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="Verify",style=discord.ButtonStyle.success,emoji="✅",custom_id="agni_verify")
    async def verify(self,interaction,button):
        rid=get_guild_setting(interaction.guild.id,"verification_role_id")
        role=interaction.guild.get_role(int(rid)) if rid else None
        if not role:return await interaction.response.send_message("❌ Verification is not configured.",ephemeral=True)
        try: await interaction.user.add_roles(role,reason="Agni verification"); await interaction.response.send_message("✅ You are verified.",ephemeral=True)
        except discord.Forbidden: await interaction.response.send_message("❌ I cannot assign the verification role.",ephemeral=True)

@bot.hybrid_command(name="setupverification",aliases=["sv"],description="Create a verification panel in the current channel.")
@permission_check("administrator")
@app_permission_check("administrator")
async def setupverification_cmd(ctx,role:discord.Role):
    set_guild_setting(ctx.guild.id,"verification_channel_id",ctx.channel.id); set_guild_setting(ctx.guild.id,"verification_role_id",role.id); await ctx.send(embed=discord.Embed(title="🔐 Server Verification",description="Click **Verify** to receive access.",color=discord.Color.green()),view=VerifyView()); await send_mod_result(ctx,"✅ Verification panel created.",True)

# Reaction roles: lightweight button role panel.
class RolePanelView(discord.ui.View):
    def __init__(self, role_id):
        super().__init__(timeout=None)
        self.role_id = int(role_id)
        button = discord.ui.Button(label="Get Role", style=discord.ButtonStyle.primary, emoji="🎭", custom_id=f"agni_role:{self.role_id}")
        button.callback = self._role_button
        self.add_item(button)

    async def _role_button(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(self.role_id)
        if not role:
            return await interaction.response.send_message("❌ Role no longer exists.", ephemeral=True)
        try:
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role, reason="Agni role panel")
                text = "removed"
            else:
                await interaction.user.add_roles(role, reason="Agni role panel")
                text = "added"
            await interaction.response.send_message(f"✅ Role **{role.name}** {text}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I cannot manage that role.", ephemeral=True)

@bot.hybrid_command(name="rolepanel",aliases=["rp"],description="Create a button role panel.")
@permission_check("administrator")
@app_permission_check("administrator")
async def rolepanel_cmd(ctx,role:discord.Role):
    if ctx.guild.me and role>=ctx.guild.me.top_role:return await send_mod_result(ctx,"❌ I cannot manage that role.",True)
    msg = await ctx.send(embed=discord.Embed(title="🎭 Role Panel",description=f"Click below to toggle {role.mention}.",color=discord.Color.blue()),view=RolePanelView(role.id))
    try:
        DB.execute("INSERT INTO role_panels(guild_id,channel_id,message_id,role_id) VALUES (?,?,?,?)",(ctx.guild.id,ctx.channel.id,msg.id,role.id)); DB.commit()
    except Exception as e: print(f"[ROLE PANEL DB ERROR] {e}")

# Lockdown
@bot.hybrid_command(name="lockdown",aliases=["ld"],description="Lock all text channels for @everyone.")
@permission_check("administrator")
@app_permission_check("administrator")
async def lockdown_cmd(ctx):
    changed=0
    for ch in ctx.guild.text_channels:
        try:
            ow=ch.overwrites_for(ctx.guild.default_role); ow.send_messages=False; await ch.set_permissions(ctx.guild.default_role,overwrite=ow,reason=f"Lockdown by {ctx.author}"); changed+=1
        except Exception: pass
    set_guild_setting(ctx.guild.id,"lockdown",1); await send_mod_result(ctx,f"🔒 Server lockdown enabled on **{changed}** channels.")

@bot.hybrid_command(name="unlockdown",aliases=["ud"],description="Unlock all text channels for @everyone.")
@permission_check("administrator")
@app_permission_check("administrator")
async def unlockdown_cmd(ctx):
    changed=0
    for ch in ctx.guild.text_channels:
        try:
            ow=ch.overwrites_for(ctx.guild.default_role); ow.send_messages=None; await ch.set_permissions(ctx.guild.default_role,overwrite=ow,reason=f"Unlockdown by {ctx.author}"); changed+=1
        except Exception: pass
    set_guild_setting(ctx.guild.id,"lockdown",0); await send_mod_result(ctx,f"🔓 Server lockdown disabled on **{changed}** channels.")

# Complete help menu. Existing module help commands remain available.
@bot.hybrid_command(name="help",description="Show Agni's all-rounder command menu.")
async def help_cmd(ctx):
    e=discord.Embed(title="🔥 Agni — All-Rounder Help",description="Use slash commands or the configured prefix. Commands are grouped below.",color=discord.Color.blurple())
    fields=[
      ("🛡️ Moderation","`ban` `kick` `unban` `softban` `mute` `removetimeout` `warn` `warnings` `clearwarnings` `history` `purge` `slowmode` `lock` `unlock` `role` `nick`"),
      ("🎙️ Voice","`deafen` `undeafen` `voicemute` `voiceunmute` `move` `voiceleaderboard`"),
      ("🚨 Security / AutoMod","`set_ban_limit` `set_channel_limit` `set_spam_limit` `automod` `automod_status` `automod_reset` `raidmode` `setraid` `raidstatus` `raidaction` `lockdown` `unlockdown` `setlog`"),
      ("🎫 Tickets","`setup_ticket` `edit_ticket` `ticket_log_channel` `set_ticket_ping`"),
      ("🎉 Giveaways","`giveaway` `reroll` `giveawayfixed` `giveaway_help`"),
      ("👋 Community","`setup_welcome` `disable_welcome` `setautorole` `setgoodbye` `afk` `suggest` `setsuggestions` `poll` `setupverification` `rolepanel`"),
      ("📊 Stats","`stats` `messagecount` `leaderboard` `messageleaderboard` `voiceleaderboard` `invites` `inviter` `inviteinfo` `inviteleaderboard` `membercount`"),
      ("💰 Economy","`balance` `pay` — 1 coin/message + 100 coins/new valid invite"),
      ("🔎 Information","`serverinfo` `roleinfo` `userinfo` `channelinfo` `avatar` `servericon` `permissions`"),
      ("⚙️ Admin","`set_prefix` `dmall` `setup_invitelog` `setvclog` `logging`"),
    ]
    for n,v in fields:e.add_field(name=n,value=v,inline=False)
    e.set_footer(text="Agni • All-rounder Discord bot")
    await ctx.send(embed=e)

# Register persistent verification/role views on startup in addition to ticket views.
_old_ready = bot.get_cog if False else None

@bot.hybrid_command(name="serverstats", aliases=["ss"], description="Show server activity and infrastructure statistics.")
async def serverstats_cmd(ctx):
    g=ctx.guild
    rows=DB.execute("SELECT COALESCE(SUM(messages),0) messages, COALESCE(SUM(coins),0) coins, COALESCE(SUM(xp),0) xp FROM user_stats WHERE guild_id=?",(g.id,)).fetchone()
    e=discord.Embed(title=f"📈 Server Statistics — {g.name}",color=discord.Color.blurple())
    e.add_field(name="Members",value=f"{g.member_count:,}"); e.add_field(name="Channels",value=f"{len(g.channels):,}"); e.add_field(name="Roles",value=f"{len(g.roles):,}")
    e.add_field(name="Tracked Messages",value=f"{rows['messages']:,}"); e.add_field(name="Community XP",value=f"{rows['xp']:,}"); e.add_field(name="Economy Coins",value=f"{rows['coins']:,}")
    await ctx.send(embed=e)


# =========================================================
# INFORMATION COMMANDS
# =========================================================

@bot.hybrid_command(name="serverinfo", aliases=["si"], description="Show information about the current server.")
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


@bot.hybrid_command(name="roleinfo", aliases=["ri"], description="List all roles in the current server.")
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


@bot.hybrid_command(name="userinfo", aliases=["ui"], description="Show information about a server member.")
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


@bot.hybrid_command(name="channelinfo", aliases=["ci"], description="Show information about a channel.")
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


@bot.hybrid_command(name="servericon", aliases=["sic"], description="Show the current server icon.")
async def servericon(ctx):
    if not ctx.guild: return await ctx.send("❌ This command can only be used inside a server.")
    if not ctx.guild.icon: return await ctx.send("❌ This server does not have a server icon.")
    embed = discord.Embed(title=f"{ctx.guild.name} — Server Icon", color=discord.Color.blue())
    embed.set_image(url=ctx.guild.icon.url)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="membercount", aliases=["mc"], description="Show the current server member count.")
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
        name="/giveaway <prize> <duration> [winners]",
        value="Random winner giveaway. Slash + prefix. Fixed winner: prefix-only `,giveawayfixed <duration> @member [winners] <prize>`. Reroll with `/reroll` or prefix.",
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
# VOICE LOGS / ADVANCED EVENT LOGGING
# =========================================================

@bot.hybrid_command(name="setvclog", aliases=["vclog", "vclogs"], description="Set the channel for voice activity logs.")
@permission_check("administrator")
@app_permission_check("administrator")
async def setvclog_cmd(ctx, channel: discord.TextChannel = None):
    if channel is None:
        set_guild_setting(ctx.guild.id, "log_channel_id", None)
        return await send_mod_result(ctx, "❌ Voice logging cannot be enabled without a channel. Use `/setvclog #channel`.", True)
    set_guild_setting(ctx.guild.id, "log_channel_id", channel.id)
    await send_mod_result(ctx, f"🎙️ Voice/event logs will be sent to {channel.mention}.")

@bot.hybrid_command(name="logging", aliases=["logs", "logstatus"], description="Show the current Agni logging configuration.")
@permission_check("administrator")
@app_permission_check("administrator")
async def logging_cmd(ctx):
    cid = get_guild_setting(ctx.guild.id, "log_channel_id")
    iid = get_guild_setting(ctx.guild.id, "invite_log_channel_id")
    gid = get_guild_setting(ctx.guild.id, "goodbye_channel_id")
    sid = get_guild_setting(ctx.guild.id, "suggestion_channel_id")
    lines = [
        f"**Event/Moderation logs:** {f'<#{cid}>' if cid else 'Not configured'}",
        f"**Invite logs:** {f'<#{iid}>' if iid else 'Not configured'}",
        f"**Join/Leave logs:** {f'<#{cid}>' if cid else 'Not configured'}",
        f"**Voice logs:** {f'<#{cid}>' if cid else 'Not configured'}",
        f"**Goodbye channel:** {f'<#{gid}>' if gid else 'Not configured'}",
        f"**Suggestions:** {f'<#{sid}>' if sid else 'Not configured'}",
    ]
    await ctx.send(embed=discord.Embed(title="📜 Agni Logging Status", description="\n".join(lines), color=discord.Color.blurple()))

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot or not member.guild:
        return

    # Accumulate voice time for the member.
    key = (member.guild.id, member.id)
    now = datetime.now(timezone.utc)
    if before.channel is None and after.channel is not None:
        voice_join_times[key] = now
    elif before.channel is not None and after.channel is None:
        started = voice_join_times.pop(key, None)
        if started:
            seconds = max(0, int((now - started).total_seconds()))
            ensure_user(member.guild.id, member.id)
            db_exec("UPDATE user_stats SET voice_seconds=voice_seconds+? WHERE guild_id=? AND user_id=?", (seconds, member.guild.id, member.id))

    # Log joins, leaves and moves.
    if before.channel is None and after.channel is not None:
        await send_log(member.guild, "🎙️ Voice Joined", f"**Member:** {member.mention}\n**Channel:** {after.channel.mention}", discord.Color.green())
    elif before.channel is not None and after.channel is None:
        await send_log(member.guild, "🎙️ Voice Left", f"**Member:** {member.mention}\n**Channel:** {before.channel.mention}", discord.Color.red())
    elif before.channel != after.channel:
        await send_log(member.guild, "🔀 Voice Channel Moved", f"**Member:** {member.mention}\n**From:** {before.channel.mention if before.channel else 'None'}\n**To:** {after.channel.mention if after.channel else 'None'}", discord.Color.orange())

    # Log self/server mute/deafen changes.
    changes = []
    if before.self_mute != after.self_mute:
        changes.append(f"Self mute: **{after.self_mute}**")
    if before.self_deaf != after.self_deaf:
        changes.append(f"Self deafen: **{after.self_deaf}**")
    if before.mute != after.mute:
        changes.append(f"Server mute: **{after.mute}**")
    if before.deaf != after.deaf:
        changes.append(f"Server deafen: **{after.deaf}**")
    if changes:
        await send_log(member.guild, "🎙️ Voice State Updated", f"**Member:** {member.mention}\n" + "\n".join(changes), discord.Color.gold())

@bot.event
async def on_member_remove_with_logging(member):
    # Kept as a named helper for installations that import this module.
    await send_log(member.guild, "📤 Member Left", f"**Member:** {member.mention} (`{member.id}`)", discord.Color.red())

# The existing on_member_remove handler above handles goodbye/invite bookkeeping.
# Add the leave log directly through a wrapper-safe helper called from the current handler.
_original_member_remove = bot.get_listener("on_member_remove") if hasattr(bot, "get_listener") else None

# Explicit command for checking voice leaderboard without relying on a category argument.
@bot.hybrid_command(name="voiceleaderboard", aliases=["vclb", "vleaderboard"], description="Show the server voice activity leaderboard.")
async def voiceleaderboard_cmd(ctx):
    rows = DB.execute("SELECT user_id,voice_seconds FROM user_stats WHERE guild_id=? ORDER BY voice_seconds DESC LIMIT 10", (ctx.guild.id,)).fetchall()
    lines = []
    for i, row in enumerate(rows, 1):
        lines.append(f"**{i}.** <@{row['user_id']}> — `{fmt_seconds(row['voice_seconds'])}`")
    await ctx.send(embed=discord.Embed(title="🎙️ Voice Leaderboard", description="\n".join(lines) or "No voice data yet.", color=discord.Color.gold()))

# More useful poll aliases while keeping the existing simple /poll command.
@bot.hybrid_command(name="quickpoll", aliases=["qpoll"], description="Create a yes/no poll quickly.")
async def quickpoll_cmd(ctx, question: str):
    embed = discord.Embed(title="📊 Poll", description=question, color=discord.Color.blurple())
    embed.set_footer(text=f"Poll by {ctx.author.display_name}")
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("👍")
    await msg.add_reaction("👎")


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
