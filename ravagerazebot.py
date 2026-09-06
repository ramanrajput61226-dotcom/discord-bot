import os
import re
import json
import random
import asyncio
import sqlite3
import logging
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

try:
    from keep_alive import keep_alive
except ImportError:
    def keep_alive():
        pass

# ============================================================
# CONFIG
# ============================================================
OWNER_ID = 1255544682759323680
DB_FILE = os.getenv("BOT_DB", "bot_data.sqlite3")
LEGACY_CONFIG = "bot_settings.json"
PREFIX_DEFAULT = ","

# The random-winner giveaway remains hybrid (prefix + slash), while fixed-winner
# giveaways remain prefix-only.

# These are the three live Discord messages supplied for migration/recovery.
# The bot will inspect them on startup. Giveaway messages are recovered from
# their embed timestamp/footer + current reaction participants.
RECOVERY_MESSAGES = [
    (1543323334756794399, 1545318942120222770, 1545319711821275157),
    (1543323334756794399, 1543323335713099885, 1545316530278109237),
    (1288817064026570752, 1509386176388399184, 1545798013128015990),
]

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
log = logging.getLogger("allrounder")

# ============================================================
# DATABASE
# ============================================================
class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = asyncio.Lock()
        self._init()

    def _init(self):
        self.conn.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS guild_config (
            guild_id INTEGER PRIMARY KEY,
            prefix TEXT NOT NULL DEFAULT ',',
            ban_limit INTEGER NOT NULL DEFAULT 5,
            channel_limit INTEGER NOT NULL DEFAULT 3,
            spam_limit INTEGER NOT NULL DEFAULT 5,
            ticket_log_channel INTEGER,
            invite_log_channel INTEGER,
            welcome_channel INTEGER,
            welcome_message TEXT,
            welcome_image TEXT,
            welcome_enabled INTEGER NOT NULL DEFAULT 0,
            goodbye_message TEXT,
            goodbye_image TEXT,
            goodbye_enabled INTEGER NOT NULL DEFAULT 0,
            goodbye_channel INTEGER,
            autorole_id INTEGER,
            bot_autorole_id INTEGER,
            modlog_channel INTEGER,
            log_channel INTEGER,
            suggestion_channel INTEGER,
            verification_channel INTEGER,
            verification_role INTEGER,
            unverified_role INTEGER,
            lockdown INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS ticket_panels (
            panel_id TEXT PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER,
            message_id INTEGER,
            category_id INTEGER,
            role_id INTEGER,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            ticket_title TEXT NOT NULL,
            ticket_description TEXT NOT NULL,
            opening_message TEXT NOT NULL,
            footer TEXT,
            color INTEGER NOT NULL DEFAULT 3447003,
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS ticket_buttons (
            button_id TEXT PRIMARY KEY,
            panel_id TEXT NOT NULL,
            label TEXT NOT NULL,
            questions TEXT NOT NULL DEFAULT '[]',
            FOREIGN KEY(panel_id) REFERENCES ticket_panels(panel_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS tickets (
            channel_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            owner_id INTEGER NOT NULL,
            panel_id TEXT,
            button_id TEXT,
            opened_at TEXT NOT NULL,
            claimed_by INTEGER,
            closed_at TEXT,
            close_reason TEXT,
            status TEXT NOT NULL DEFAULT 'open'
        );
        CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            moderator_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS mod_cases (
            case_id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            moderator_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS message_stats (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            total INTEGER NOT NULL DEFAULT 0,
            daily INTEGER NOT NULL DEFAULT 0,
            weekly INTEGER NOT NULL DEFAULT 0,
            monthly INTEGER NOT NULL DEFAULT 0,
            last_message TEXT,
            PRIMARY KEY(guild_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS invite_stats (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            total INTEGER NOT NULL DEFAULT 0,
            valid INTEGER NOT NULL DEFAULT 0,
            fake INTEGER NOT NULL DEFAULT 0,
            left_count INTEGER NOT NULL DEFAULT 0,
            rejoins INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(guild_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS member_invites (
            guild_id INTEGER NOT NULL,
            member_id INTEGER NOT NULL,
            inviter_id INTEGER,
            invite_code TEXT,
            joined_at TEXT NOT NULL,
            left_at TEXT,
            PRIMARY KEY(guild_id,member_id)
        );
        CREATE TABLE IF NOT EXISTS giveaways (
            message_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            prize TEXT NOT NULL,
            winners INTEGER NOT NULL DEFAULT 1,
            host_id INTEGER NOT NULL,
            fixed_winner_id INTEGER,
            end_at TEXT NOT NULL,
            ended INTEGER NOT NULL DEFAULT 0,
            recovered INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS automod_rules (
            guild_id INTEGER PRIMARY KEY,
            bad_words TEXT NOT NULL DEFAULT '[]',
            blocked_links INTEGER NOT NULL DEFAULT 0,
            blocked_invites INTEGER NOT NULL DEFAULT 0,
            mention_limit INTEGER NOT NULL DEFAULT 5,
            caps_percent INTEGER NOT NULL DEFAULT 80,
            repeated_limit INTEGER NOT NULL DEFAULT 3,
            emoji_limit INTEGER NOT NULL DEFAULT 15,
            attachment_filter INTEGER NOT NULL DEFAULT 0,
            auto_delete INTEGER NOT NULL DEFAULT 1,
            auto_warn INTEGER NOT NULL DEFAULT 0,
            auto_timeout INTEGER NOT NULL DEFAULT 0,
            auto_kick INTEGER NOT NULL DEFAULT 0,
            auto_ban INTEGER NOT NULL DEFAULT 0,
            whitelist_roles TEXT NOT NULL DEFAULT '[]',
            whitelist_channels TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS reaction_roles (
            message_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            emoji TEXT NOT NULL,
            PRIMARY KEY(message_id,role_id,emoji)
        );
        CREATE TABLE IF NOT EXISTS suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS afk (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(guild_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS polls (
            message_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            options TEXT NOT NULL,
            ends_at TEXT,
            ended INTEGER NOT NULL DEFAULT 0
        );
        """)
        self.conn.commit()

    def execute(self, sql, params=()):
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    def fetchone(self, sql, params=()):
        return self.conn.execute(sql, params).fetchone()

    def fetchall(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()

    def guild_defaults(self, guild_id):
        self.execute("INSERT OR IGNORE INTO guild_config(guild_id) VALUES(?)", (guild_id,))
        return self.fetchone("SELECT * FROM guild_config WHERE guild_id=?", (guild_id,))

    def set_guild(self, guild_id, key, value):
        allowed = {r[1] for r in self.conn.execute("PRAGMA table_info(guild_config)")}
        if key not in allowed:
            raise ValueError("invalid guild config key")
        self.execute(f"UPDATE guild_config SET {key}=? WHERE guild_id=?", (value, guild_id))


db = Database(DB_FILE)

# Extra v3 persistence (kept separate so existing v2 databases migrate safely).
db.conn.executescript("""
CREATE TABLE IF NOT EXISTS custom_commands (guild_id INTEGER NOT NULL, name TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(guild_id,name));
CREATE TABLE IF NOT EXISTS reaction_roles_v3 (guild_id INTEGER NOT NULL, message_id INTEGER NOT NULL, emoji TEXT NOT NULL, role_id INTEGER NOT NULL, PRIMARY KEY(guild_id,message_id,emoji));
CREATE TABLE IF NOT EXISTS verification_config (guild_id INTEGER PRIMARY KEY, channel_id INTEGER, role_id INTEGER, enabled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS raid_config (guild_id INTEGER PRIMARY KEY, join_limit INTEGER NOT NULL DEFAULT 8, window_seconds INTEGER NOT NULL DEFAULT 20, enabled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS join_history (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, joined_at TEXT NOT NULL);
""")
db.conn.commit()

# ============================================================
# V4 DATABASE MIGRATIONS
# ============================================================
# Safe ALTERs for databases created by earlier versions.
def _add_column(table, column, definition):
    cols={r[1] for r in db.conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        db.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

_add_column("guild_config","goodbye_channel","INTEGER")
_add_column("message_stats","xp","INTEGER NOT NULL DEFAULT 0")
_add_column("message_stats","level","INTEGER NOT NULL DEFAULT 0")
_add_column("raid_config","action","TEXT NOT NULL DEFAULT 'lockdown'")
db.conn.executescript("""
CREATE TABLE IF NOT EXISTS economy (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, balance INTEGER NOT NULL DEFAULT 0, bank INTEGER NOT NULL DEFAULT 0, last_daily TEXT, last_work TEXT, PRIMARY KEY(guild_id,user_id));
CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, remind_at TEXT NOT NULL, text TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS level_config (guild_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1, xp_min INTEGER NOT NULL DEFAULT 10, xp_max INTEGER NOT NULL DEFAULT 20, cooldown INTEGER NOT NULL DEFAULT 60, announce_channel INTEGER, announce_message TEXT DEFAULT '{user} reached level {level}!');
CREATE TABLE IF NOT EXISTS level_rewards (guild_id INTEGER NOT NULL, level INTEGER NOT NULL, role_id INTEGER NOT NULL, PRIMARY KEY(guild_id,level));
CREATE TABLE IF NOT EXISTS starboard_config (guild_id INTEGER PRIMARY KEY, channel_id INTEGER, emoji TEXT NOT NULL DEFAULT '⭐', threshold INTEGER NOT NULL DEFAULT 5, enabled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS starboard_posts (guild_id INTEGER NOT NULL, message_id INTEGER NOT NULL, starboard_message_id INTEGER NOT NULL, PRIMARY KEY(guild_id,message_id));
CREATE TABLE IF NOT EXISTS reaction_role_panels (guild_id INTEGER NOT NULL, message_id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sticky_messages (channel_id INTEGER PRIMARY KEY, message_id INTEGER, content TEXT NOT NULL);
""")
db.conn.commit()

# ============================================================
# HELPERS
# ============================================================
def utcnow():
    return datetime.now(timezone.utc)

def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()

def parse_iso(value):
    return datetime.fromisoformat(value)

def parse_time(value: str) -> int:
    m = re.fullmatch(r"\s*(\d+)\s*([smhdw])\s*", value.lower())
    if not m:
        raise ValueError("Use formats like 30s, 10m, 2h, 1d or 1w.")
    n = int(m.group(1)); unit = m.group(2)
    return n * {"s":1,"m":60,"h":3600,"d":86400,"w":604800}[unit]

def is_whitelisted(user, guild):
    return bool(user.id == OWNER_ID or getattr(user.guild_permissions, "administrator", False))

def has_perm(member, permission):
    return member.id == OWNER_ID or bool(getattr(member.guild_permissions, permission, False))

def hierarchy_error(ctx, target):
    if target == ctx.author: return "❌ You cannot use this command on yourself."
    if target == ctx.guild.owner: return "❌ You cannot act on the server owner."
    if ctx.guild.me and target.top_role >= ctx.guild.me.top_role:
        return "❌ I cannot act on a member whose highest role is equal to or higher than mine."
    if ctx.author.id != OWNER_ID and target.top_role >= ctx.author.top_role:
        return "❌ You cannot act on a member with an equal or higher role than yours."
    return None

def command_check(permission):
    async def predicate(ctx):
        return bool(ctx.guild and has_perm(ctx.author, permission))
    return commands.check(predicate)

def app_command_check(permission):
    async def predicate(interaction):
        return bool(interaction.guild and isinstance(interaction.user, discord.Member) and has_perm(interaction.user, permission))
    return app_commands.check(predicate)

def embed_color(value):
    try: return discord.Color(int(str(value).replace("#", ""), 16))
    except Exception: return discord.Color.blurple()

def safe_format(text, member, guild, inviter="Unknown"):
    try:
        return text.format(user=member.mention, username=member.display_name, server=guild.name,
                           count=guild.member_count, inviter=inviter)
    except Exception:
        return text

# ============================================================
# BOT / RUNTIME STATE
# ============================================================
intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.invites = True
intents.message_content = True
intents.presences = False

bot = commands.Bot(command_prefix=lambda b, m: commands.when_mentioned_or(
    (db.guild_defaults(m.guild.id)["prefix"] if m.guild else PREFIX_DEFAULT))(b, m),
    intents=intents, help_command=None)

invite_cache = {}
spam_tracker = defaultdict(deque)
repeat_tracker = defaultdict(deque)
voice_join_times = {}
loaded_views = set()
xp_cooldowns = {}
reminder_task_started = False


# ============================================================
# LEGACY JSON MIGRATION
# ============================================================
def migrate_legacy_json():
    if not os.path.exists(LEGACY_CONFIG):
        return
    marker = "legacy_migrated"
    if db.fetchone("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (marker,)):
        return
    try:
        with open(LEGACY_CONFIG, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Legacy globals were not per-guild. Preserve them in a global pseudo-config
        # only where there is an unambiguous current guild; actual guild-specific
        # values are retained in the legacy file until the bot has observed the guild.
        # Ticket configs are migrated into per-panel SQLite rows using their keys.
        for guild in list(bot.guilds):
            row = db.guild_defaults(guild.id)
            updates = {
                "ban_limit": data.get("ban_limit", row["ban_limit"]),
                "channel_limit": data.get("channel_limit", row["channel_limit"]),
                "spam_limit": data.get("spam_limit", row["spam_limit"]),
                "ticket_log_channel": data.get("ticket_log_channel_id"),
                "invite_log_channel": data.get("invite_log_channel_id"),
                "welcome_channel": data.get("welcome_channel_id"),
                "welcome_message": data.get("custom_welcome_msg"),
                "welcome_image": data.get("custom_welcome_img"),
                "welcome_enabled": 1 if data.get("welcome_enabled") else 0,
                "prefix": data.get("custom_prefix", PREFIX_DEFAULT),
            }
            for k,v in updates.items():
                db.set_guild(guild.id, k, v)
        # Old ticket_configs were keyed by guild id.
        for gid_raw, raw in data.get("ticket_configs", {}).items():
            gid = int(gid_raw)
            if "panels" not in raw:
                pid = f"legacy_panel_{gid}_{random.randint(1000,9999)}"
                panels = {pid: raw}
            else:
                panels = raw.get("panels", {})
            for pid, panel in panels.items():
                if db.fetchone("SELECT panel_id FROM ticket_panels WHERE panel_id=?", (pid,)):
                    continue
                db.execute("""INSERT INTO ticket_panels
                    (panel_id,guild_id,category_id,role_id,title,description,ticket_title,ticket_description,opening_message,footer,color)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (pid,gid,panel.get("category_id"),panel.get("role_id"),
                    panel.get("title","Support Hub"),panel.get("desc","Click a button below to open a support ticket."),
                    panel.get("ticket_title","🎫 {type} Ticket"),panel.get("ticket_desc","Ticket opened by {user}\nPlease wait patiently."),
                    panel.get("ticket_open_message","{role} New ticket opened by {user}"),panel.get("ticket_footer","Opened by {user}"),int(panel.get("ticket_color",3447003))))
                for bid,b in panel.get("buttons",{}).items():
                    db.execute("INSERT OR REPLACE INTO ticket_buttons(button_id,panel_id,label,questions) VALUES(?,?,?,?)",
                               (bid,pid,b.get("label","Support"),json.dumps(b.get("questions",[])[:4])))
        db.execute("CREATE TABLE IF NOT EXISTS legacy_migrated(name TEXT PRIMARY KEY)")
        db.execute("INSERT OR IGNORE INTO legacy_migrated(name) VALUES('done')")
        log.info("Legacy bot_settings.json migration completed into SQLite.")
    except Exception as e:
        log.exception("Legacy migration failed: %s", e)

# ============================================================
# LOGGING / MOD CASES
# ============================================================
async def get_log_channel(guild, kind="log"):
    row = db.guild_defaults(guild.id)
    cid = row["modlog_channel"] if kind == "mod" else row["log_channel"]
    return guild.get_channel(cid) if cid else None

async def server_log(guild, title, description, color=discord.Color.blurple(), kind="log"):
    channel = await get_log_channel(guild, kind)
    if not channel: return
    try:
        e = discord.Embed(title=title, description=description, color=color, timestamp=utcnow())
        await channel.send(embed=e)
    except Exception as e:
        log.warning("log send failed: %s", e)

def new_case(guild_id, user_id, moderator_id, action, reason):
    cur = db.execute("INSERT INTO mod_cases(guild_id,user_id,moderator_id,action,reason,created_at) VALUES(?,?,?,?,?,?)",
                     (guild_id,user_id,moderator_id,action,reason,iso(utcnow())))
    return cur.lastrowid

# ============================================================
# TICKET SYSTEM
# ============================================================
def panel_row(guild_id, panel_id=None):
    if panel_id:
        return db.fetchone("SELECT * FROM ticket_panels WHERE guild_id=? AND panel_id=?", (guild_id,panel_id))
    return db.fetchone("SELECT * FROM ticket_panels WHERE guild_id=? AND active=1 ORDER BY rowid DESC LIMIT 1", (guild_id,))

def panel_buttons(panel_id):
    rows = db.fetchall("SELECT * FROM ticket_buttons WHERE panel_id=? ORDER BY rowid", (panel_id,))
    out = []
    for r in rows:
        try: qs=json.loads(r["questions"])
        except Exception: qs=[]
        out.append((r["button_id"],r["label"],qs))
    return out

class CloseTicketModal(discord.ui.Modal, title="Close Ticket"):
    reason = discord.ui.TextInput(label="Closing Reason", style=discord.TextStyle.paragraph, required=True, max_length=1000)
    async def on_submit(self, interaction):
        row = db.fetchone("SELECT * FROM tickets WHERE channel_id=? AND status='open'", (interaction.channel_id,))
        if not row:
            return await interaction.response.send_message("❌ This is not an active ticket.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        reason = self.reason.value.strip()
        channel = interaction.channel
        messages = [m async for m in channel.history(limit=None, oldest_first=True)]
        lines = [f"Ticket: #{channel.name}",f"Server: {interaction.guild.name}",f"Closed By: {interaction.user} ({interaction.user.id})",
                 f"Closed At: {utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",f"Reason: {reason}",""]
        for m in messages:
            body = m.content or "[Embed/Component]"
            if m.attachments: body += " | " + " | ".join(a.url for a in m.attachments)
            lines.append(f"[{m.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}] {m.author} ({m.author.id}): {body}")
        path=f"transcript_{channel.id}.txt"
        with open(path,"w",encoding="utf-8") as f: f.write("\n".join(lines))
        db.execute("UPDATE tickets SET status='closed',closed_at=?,close_reason=? WHERE channel_id=?",(iso(utcnow()),reason,channel.id))
        owner=interaction.guild.get_member(row["owner_id"])
        file_obj=discord.File(path,filename=f"{channel.name}_transcript.txt")
        if owner:
            try: await owner.send(f"🔒 **Ticket Closed**\n**Server:** {interaction.guild.name}\n**Reason:** {reason}",file=file_obj)
            except Exception: pass
        try:
            logc=interaction.guild.get_channel(db.guild_defaults(interaction.guild.id)["ticket_log_channel"] or 0)
            if logc: await logc.send(content=f"📁 **Closed Ticket:** `#{channel.name}`\n**Closed By:** {interaction.user.mention}\n**Reason:** {reason}",file=discord.File(path,filename=f"{channel.name}_transcript.txt"))
        except Exception: pass
        try: os.remove(path)
        except Exception: pass
        await interaction.followup.send("🔒 Ticket closed and transcript processed.", ephemeral=True)
        await asyncio.sleep(2)
        try: await channel.delete(reason=f"Ticket closed by {interaction.user}: {reason}")
        except Exception: pass

class TicketControlView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Close Ticket",style=discord.ButtonStyle.danger,emoji="🔒",custom_id="ticket:close")
    async def close(self,interaction,button):
        row=db.fetchone("SELECT * FROM tickets WHERE channel_id=? AND status='open'",(interaction.channel_id,))
        if not row: return await interaction.response.send_message("❌ Not an active ticket.",ephemeral=True)
        if interaction.user.id != row["owner_id"] and not (isinstance(interaction.user,discord.Member) and (interaction.user.guild_permissions.manage_channels or interaction.user.guild_permissions.administrator)):
            return await interaction.response.send_message("❌ You cannot close this ticket.",ephemeral=True)
        await interaction.response.send_modal(CloseTicketModal())

class TicketOpenModal(discord.ui.Modal):
    def __init__(self,panel_id,button_id,label,questions):
        super().__init__(title=f"{label[:35]} Ticket")
        self.panel_id=panel_id; self.button_id=button_id; self.label_text=label; self.inputs=[]
        for q in questions[:4]:
            x=discord.ui.TextInput(label=q[:45],style=discord.TextStyle.paragraph,required=False,max_length=500)
            self.inputs.append((q,x)); self.add_item(x)
    async def on_submit(self,interaction):
        await interaction.response.defer(ephemeral=True)
        await open_ticket(interaction,self.panel_id,self.button_id,self.label_text,[(q,x.value.strip()) for q,x in self.inputs])

class TicketButton(discord.ui.Button):
    def __init__(self,panel_id,button_id,label,questions):
        super().__init__(label=label[:80],style=discord.ButtonStyle.primary,custom_id=f"ticket:open:{panel_id}:{button_id}")
        self.panel_id=panel_id; self.button_id=button_id; self.questions=questions
    async def callback(self,interaction):
        if self.questions: return await interaction.response.send_modal(TicketOpenModal(self.panel_id,self.button_id,self.label,self.questions))
        await interaction.response.defer(ephemeral=True); await open_ticket(interaction,self.panel_id,self.button_id,self.label,[])

class TicketPanelView(discord.ui.View):
    def __init__(self,panel_id):
        super().__init__(timeout=None)
        for bid,label,qs in panel_buttons(panel_id)[:25]: self.add_item(TicketButton(panel_id,bid,label,qs))

async def open_ticket(interaction,panel_id,button_id,label,answers):
    guild=interaction.guild; user=interaction.user; panel=panel_row(guild.id,panel_id)
    if not panel: return await interaction.followup.send("❌ Ticket panel no longer exists.",ephemeral=True)
    existing=db.fetchone("SELECT channel_id FROM tickets WHERE guild_id=? AND owner_id=? AND status='open'",(guild.id,user.id))
    if existing:
        ch=guild.get_channel(existing["channel_id"]); return await interaction.followup.send(f"❌ You already have an open ticket: {ch.mention if ch else existing['channel_id']}",ephemeral=True)
    category=guild.get_channel(panel["category_id"]) if panel["category_id"] else None; role=guild.get_role(panel["role_id"]) if panel["role_id"] else None
    overwrites={guild.default_role:discord.PermissionOverwrite(view_channel=False),user:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True,embed_links=True)}
    if guild.me: overwrites[guild.me]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,manage_channels=True,manage_messages=True,attach_files=True,embed_links=True)
    if role: overwrites[role]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True,embed_links=True)
    clean=re.sub(r"[^a-z0-9_-]","",user.name.lower())[:30] or "user"
    try:
        ch=await guild.create_text_channel(f"ticket-{clean}-{user.id}",category=category,overwrites=overwrites,reason=f"Ticket opened by {user}")
        e=discord.Embed(title=panel["ticket_title"].replace("{type}",label).replace("{user}",user.mention),description=panel["ticket_description"].replace("{user}",user.mention).replace("{username}",user.display_name),color=embed_color(panel["color"]),timestamp=utcnow())
        e.add_field(name="Opened By",value=user.mention,inline=False); e.add_field(name="Ticket Type",value=label,inline=False)
        for q,a in answers: e.add_field(name=q[:256],value=a[:1024] or "Not Provided",inline=False)
        if panel["footer"]: e.set_footer(text=panel["footer"].replace("{user}",user.display_name).replace("{type}",label))
        content=panel["opening_message"].replace("{user}",user.mention).replace("{role}",role.mention if role else "@here")
        await ch.send(content=content,embed=e,view=TicketControlView())
        db.execute("INSERT INTO tickets(channel_id,guild_id,owner_id,panel_id,button_id,opened_at) VALUES(?,?,?,?,?,?)",(ch.id,guild.id,user.id,panel_id,button_id,iso(utcnow())))
        await interaction.followup.send(f"✅ Ticket created: {ch.mention}",ephemeral=True)
    except discord.Forbidden: await interaction.followup.send("❌ I need Manage Channels and Send Messages permissions.",ephemeral=True)

@bot.tree.command(name="setup_ticket",description="Create a persistent ticket panel")
@app_command_check("administrator")
@app_commands.describe(category="Ticket category",role="Support role")
async def setup_ticket(interaction,category:discord.CategoryChannel,role:discord.Role):
    pid=f"panel_{random.randint(100000,999999)}"
    db.execute("INSERT INTO ticket_panels(panel_id,guild_id,category_id,role_id,title,description,ticket_title,ticket_description,opening_message,footer,color) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
               (pid,interaction.guild.id,category.id,role.id,"Support Hub","Click a button below to open a support ticket.","🎫 {type} Ticket","Ticket opened by {user}\nPlease wait patiently.","{role} New ticket opened by {user}","Opened by {user}",3447003))
    bid=f"button_{random.randint(100000,999999)}"; db.execute("INSERT INTO ticket_buttons(button_id,panel_id,label,questions) VALUES(?,?,?,?)",(bid,pid,"Support","[]"))
    await interaction.response.send_message("✅ Ticket panel created. Use `/deploy_ticket` to post it.",ephemeral=True)

@bot.tree.command(name="deploy_ticket",description="Deploy a saved ticket panel in this channel")
@app_command_check("administrator")
async def deploy_ticket(interaction):
    p=panel_row(interaction.guild.id)
    if not p: return await interaction.response.send_message("❌ No ticket panel configured.",ephemeral=True)
    e=discord.Embed(title=p["title"],description=p["description"],color=embed_color(p["color"]))
    await interaction.response.send_message(embed=e,view=TicketPanelView(p["panel_id"]))
    msg=await interaction.original_response(); db.execute("UPDATE ticket_panels SET channel_id=?,message_id=? WHERE panel_id=?",(interaction.channel.id,msg.id,p["panel_id"]))
    bot.add_view(TicketPanelView(p["panel_id"]),message_id=msg.id)

# ============================================================
# GIVEAWAYS + LIVE RECOVERY
# ============================================================
async def pick_giveaway_winners(message,row):
    try: msg=await message.channel.fetch_message(message.id)
    except Exception: return
    reaction=discord.utils.find(lambda r:str(r.emoji)=="🎉",msg.reactions)
    ids=[]
    if reaction:
        async for u in reaction.users():
            if not u.bot: ids.append(u.id)
    ids=list(dict.fromkeys(ids)); fixed=row["fixed_winner_id"]
    chosen=[]
    if fixed and fixed in ids:
        m=msg.guild.get_member(fixed)
        if m: chosen.append(m)
        ids.remove(fixed)
    random.shuffle(ids)
    for uid in ids:
        if len(chosen)>=row["winners"]: break
        m=msg.guild.get_member(uid)
        if m: chosen.append(m)
    prize=row["prize"]
    if chosen:
        mentions=", ".join(x.mention for x in chosen)
        e=discord.Embed(title="🎉 GIVEAWAY ENDED 🎉",description=f"**Prize:** {prize}\n**Winner(s):** {mentions} 🏆\n**Hosted by:** <@{row['host_id']}>",color=discord.Color.green(),timestamp=utcnow())
        await msg.edit(embed=e,view=None); await msg.channel.send(f"🎊 Congratulations {mentions}! You won **{prize}**!")
    else:
        e=discord.Embed(title="🎉 GIVEAWAY ENDED 🎉",description=f"**Prize:** {prize}\n❌ No valid participants found.",color=discord.Color.red(),timestamp=utcnow())
        await msg.edit(embed=e,view=None)
    db.execute("UPDATE giveaways SET ended=1 WHERE message_id=?",(msg.id,))

async def recover_giveaway_message(guild_id,channel_id,message_id):
    guild=bot.get_guild(guild_id); channel=guild.get_channel(channel_id) if guild else None
    if not channel: return
    try: msg=await channel.fetch_message(message_id)
    except Exception as e: log.warning("Could not fetch recovery message %s: %s",message_id,e); return
    if not msg.embeds: return
    emb=msg.embeds[0]
    if "GIVEAWAY" not in (emb.title or "").upper(): return
    existing=db.fetchone("SELECT * FROM giveaways WHERE message_id=?",(message_id,))
    if existing and existing["ended"]: return
    desc=emb.description or ""
    prize_match=re.search(r"\*\*Prize:\*\*\s*(.+)",desc)
    winners_match=re.search(r"\*\*Winner\(s\):\*\*\s*`?(\d+)",desc)
    host_match=re.search(r"\*\*Hosted by:\*\*\s*<@!?([0-9]+)>",desc)
    if not prize_match: return
    prize=prize_match.group(1).strip(); winners=int(winners_match.group(1)) if winners_match else 1; host=int(host_match.group(1)) if host_match else (msg.author.id if msg.author else OWNER_ID)
    # Original code stored only a relative footer. The embed timestamp gives us the start time.
    footer=emb.footer.text if emb.footer else ""
    dm=re.search(r"Ends in\s+(\d+)\s*([smhdw])",footer or "",re.I)
    if existing: end_at=parse_iso(existing["end_at"])
    elif dm: end_at=msg.created_at + timedelta(seconds=parse_time(dm.group(1)+dm.group(2)))
    else: return
    db.execute("INSERT OR IGNORE INTO giveaways(message_id,guild_id,channel_id,prize,winners,host_id,end_at,recovered) VALUES(?,?,?,?,?,?,?,1)",(msg.id,guild_id,channel_id,prize,winners,host,iso(end_at)))
    row=db.fetchone("SELECT * FROM giveaways WHERE message_id=?",(msg.id,))
    if end_at <= utcnow(): await pick_giveaway_winners(msg,row)

@bot.hybrid_command(name="giveaway",description="Start a giveaway (random winners; fixed winner is prefix-only)")
@command_check("manage_guild")
@app_command_check("manage_guild")
@app_commands.describe(prize="Prize",duration="30s, 10m, 2h, 1d",winners_count="Number of random winners",fixed_winner="Prefix only: guaranteed winner")
async def giveaway(ctx,prize:str,duration:str,winners_count:int=1,fixed_winner:discord.Member=None):
    if ctx.interaction is not None and fixed_winner is not None:
        return await ctx.send("❌ Fixed-winner giveaways are prefix-only. Use the bot prefix for a fixed winner.",ephemeral=True)
    if winners_count<1 or winners_count>25: return await ctx.send("❌ Winners must be between 1 and 25.")
    if fixed_winner is not None and fixed_winner.bot: return await ctx.send("❌ A bot cannot be the fixed winner.")
    try: seconds=parse_time(duration)
    except ValueError as e: return await ctx.send(f"❌ {e}")
    end=utcnow()+timedelta(seconds=seconds)
    winner_line=f"**Winner(s):** `{winners_count}`" + (f"\n**Guaranteed Winner:** {fixed_winner.mention}" if fixed_winner else "")
    e=discord.Embed(title="🎉 GIVEAWAY TIME! 🎉",description=f"**Prize:** {prize}\n{winner_line}\n**Hosted by:** {ctx.author.mention}\n\nReact with 🎉 to enter!",color=discord.Color.gold(),timestamp=utcnow())
    e.set_footer(text=f"Ends in {duration}")
    msg=await ctx.send(embed=e); await msg.add_reaction("🎉")
    db.execute("INSERT OR REPLACE INTO giveaways(message_id,guild_id,channel_id,prize,winners,host_id,fixed_winner_id,end_at) VALUES(?,?,?,?,?,?,?,?)",(msg.id,ctx.guild.id,ctx.channel.id,prize,winners_count,ctx.author.id,fixed_winner.id if fixed_winner else None,iso(end)))

@bot.tree.command(name="giveaway_end",description="End a giveaway immediately")
@app_command_check("manage_guild")
@app_commands.describe(message_id="Giveaway message ID")
async def giveaway_end(interaction,message_id:str):
    try: mid=int(message_id)
    except ValueError: return await interaction.response.send_message("❌ Invalid message ID.",ephemeral=True)
    row=db.fetchone("SELECT * FROM giveaways WHERE message_id=? AND guild_id=?",(mid,interaction.guild.id))
    if not row: return await interaction.response.send_message("❌ Giveaway not found in the database.",ephemeral=True)
    ch=interaction.guild.get_channel(row["channel_id"])
    if not ch: return await interaction.response.send_message("❌ Giveaway channel not found.",ephemeral=True)
    await interaction.response.send_message("⏳ Ending giveaway...",ephemeral=True)
    await pick_giveaway_winners(await ch.fetch_message(mid),row)

@bot.tree.command(name="giveaway_reroll",description="Reroll a giveaway")
@app_command_check("manage_guild")
@app_commands.describe(message_id="Ended giveaway message ID")
async def giveaway_reroll(interaction,message_id:str):
    try: mid=int(message_id)
    except ValueError: return await interaction.response.send_message("❌ Invalid message ID.",ephemeral=True)
    row=db.fetchone("SELECT * FROM giveaways WHERE message_id=? AND guild_id=?",(mid,interaction.guild.id))
    if not row or not row["ended"]: return await interaction.response.send_message("❌ Ended giveaway not found.",ephemeral=True)
    ch=interaction.guild.get_channel(row["channel_id"])
    if not ch: return await interaction.response.send_message("❌ Channel not found.",ephemeral=True)
    msg=await ch.fetch_message(mid); reaction=discord.utils.find(lambda r:str(r.emoji)=="🎉",msg.reactions); ids=[]
    if reaction:
        async for u in reaction.users():
            if not u.bot: ids.append(u.id)
    members=[interaction.guild.get_member(x) for x in ids]; members=[x for x in members if x]
    if not members: return await interaction.response.send_message("❌ No participants.",ephemeral=True)
    winners=random.sample(members,min(row["winners"],len(members))); mentions=", ".join(x.mention for x in winners)
    await interaction.response.send_message(f"🎉 New winner(s): {mentions}")

# ============================================================
# MODERATION: cases, warnings, core commands
# ============================================================
async def apply_mod(ctx,member,action,reason,callback):
    err=hierarchy_error(ctx,member)
    if err: return await ctx.send(err,ephemeral=True)
    if is_whitelisted(member,ctx.guild) and member.id!=ctx.author.id: return await ctx.send("❌ Access denied: target is protected.",ephemeral=True)
    try: await callback()
    except discord.Forbidden: return await ctx.send("❌ I don't have enough permissions or role hierarchy.",ephemeral=True)
    case=new_case(ctx.guild.id,member.id,ctx.author.id,action,reason)
    await server_log(ctx.guild,f"🔨 {action.title()} | Case #{case}",f"**User:** {member.mention}\n**Moderator:** {ctx.author.mention}\n**Reason:** {reason}",discord.Color.red(),"mod")
    await ctx.send(f"✅ {action.title()} successful for {member.mention}. **Case #{case}**")

@bot.hybrid_command(name="ban",description="Ban a member")
@app_command_check("ban_members")
@command_check("ban_members")
async def ban_cmd(ctx,member:discord.Member,reason:str="No reason provided"):
    await apply_mod(ctx,member,"ban",reason,lambda:member.ban(reason=reason))

@bot.hybrid_command(name="kick",description="Kick a member")
@app_command_check("kick_members")
@command_check("kick_members")
async def kick_cmd(ctx,member:discord.Member,reason:str="No reason provided"):
    await apply_mod(ctx,member,"kick",reason,lambda:member.kick(reason=reason))

@bot.hybrid_command(name="softban",description="Ban then unban a member")
@app_command_check("ban_members")
@command_check("ban_members")
async def softban(ctx,member:discord.Member,reason:str="No reason provided"):
    async def act(): await member.ban(reason=reason); await ctx.guild.unban(member,reason=f"Softban: {reason}")
    await apply_mod(ctx,member,"softban",reason,act)

@bot.hybrid_command(name="unban",description="Unban a user by ID")
@app_command_check("ban_members")
@command_check("ban_members")
async def unban(ctx,user_id:str,reason:str="No reason provided"):
    try: uid=int(user_id)
    except ValueError: return await ctx.send("❌ Invalid user ID.",ephemeral=True)
    try:
        user=await bot.fetch_user(uid); await ctx.guild.unban(user,reason=reason)
        case=new_case(ctx.guild.id,uid,ctx.author.id,"unban",reason); await ctx.send(f"✅ Unbanned **{user}**. Case #{case}")
        await server_log(ctx.guild,f"🔓 Unban | Case #{case}",f"**User:** {user}\n**Moderator:** {ctx.author.mention}\n**Reason:** {reason}",discord.Color.green(),"mod")
    except discord.NotFound: await ctx.send("❌ User is not banned or could not be found.",ephemeral=True)

@bot.hybrid_command(name="warn",description="Warn a member")
@app_command_check("moderate_members")
@command_check("moderate_members")
async def warn(ctx,member:discord.Member,reason:str="No reason provided"):
    err=hierarchy_error(ctx,member)
    if err: return await ctx.send(err,ephemeral=True)
    cur=db.execute("INSERT INTO warnings(guild_id,user_id,moderator_id,reason,created_at) VALUES(?,?,?,?,?)",(ctx.guild.id,member.id,ctx.author.id,reason,iso(utcnow())))
    case=new_case(ctx.guild.id,member.id,ctx.author.id,"warn",reason)
    await server_log(ctx.guild,f"⚠️ Warning | Case #{case}",f"**User:** {member.mention}\n**Moderator:** {ctx.author.mention}\n**Reason:** {reason}",discord.Color.orange(),"mod")
    await ctx.send(f"⚠️ {member.mention} warned. Warning #{cur.lastrowid} | Case #{case}")

@bot.hybrid_command(name="warnings",description="Show active warnings")
async def warnings(ctx,member:discord.Member=None):
    member=member or ctx.author; rows=db.fetchall("SELECT id,moderator_id,reason,created_at FROM warnings WHERE guild_id=? AND user_id=? AND active=1 ORDER BY id DESC",(ctx.guild.id,member.id))
    if not rows: return await ctx.send(f"✅ {member.mention} has no active warnings.")
    text="\n".join(f"**#{r['id']}** — {r['reason']} — <@{r['moderator_id']}> — <t:{int(parse_iso(r['created_at']).timestamp())}:R>" for r in rows)
    await ctx.send(embed=discord.Embed(title=f"Warnings — {member}",description=text[:4096],color=discord.Color.orange()))

@bot.hybrid_command(name="clearwarnings",description="Clear a member's warnings")
@app_command_check("moderate_members")
@command_check("moderate_members")
async def clearwarnings(ctx,member:discord.Member):
    db.execute("UPDATE warnings SET active=0 WHERE guild_id=? AND user_id=? AND active=1",(ctx.guild.id,member.id)); await ctx.send(f"🧹 Cleared warnings for {member.mention}.")

@bot.hybrid_command(name="history",description="Show moderation case history")
async def history(ctx,member:discord.Member=None):
    member=member or ctx.author; rows=db.fetchall("SELECT case_id,action,reason,moderator_id,created_at FROM mod_cases WHERE guild_id=? AND user_id=? ORDER BY case_id DESC LIMIT 20",(ctx.guild.id,member.id))
    text="\n".join(f"**Case #{r['case_id']}** `{r['action']}` — {r['reason']} — <@{r['moderator_id']}> — <t:{int(parse_iso(r['created_at']).timestamp())}:R>" for r in rows) or "No cases."
    await ctx.send(embed=discord.Embed(title=f"Moderation History — {member}",description=text[:4096],color=discord.Color.blurple()))

@bot.hybrid_command(name="mute",description="Timeout a member")
@app_command_check("moderate_members")
@command_check("moderate_members")
async def mute(ctx,member:discord.Member,duration:str,reason:str="No reason provided"):
    try: seconds=parse_time(duration)
    except ValueError as e: return await ctx.send(f"❌ {e}")
    await apply_mod(ctx,member,"timeout",reason,lambda:member.timeout(timedelta(seconds=seconds),reason=reason))

@bot.hybrid_command(name="unmute",aliases=["removetimeout","rto"],description="Remove timeout")
@app_command_check("moderate_members")
@command_check("moderate_members")
async def unmute(ctx,member:discord.Member,reason:str="No reason provided"):
    await apply_mod(ctx,member,"untimeout",reason,lambda:member.timeout(None,reason=reason))

@bot.hybrid_command(name="nick",description="Change a member nickname")
@app_command_check("manage_nicknames")
@command_check("manage_nicknames")
async def nick(ctx,member:discord.Member,nickname:str=None):
    err=hierarchy_error(ctx,member)
    if err: return await ctx.send(err,ephemeral=True)
    old=member.display_name; await member.edit(nick=nickname,reason=f"Nickname changed by {ctx.author}"); await ctx.send(f"✅ Nickname changed: `{old}` → `{nickname or member.name}`")

@bot.hybrid_command(name="mystats",description="Show your message/invite/voice stats")
async def mystats(ctx):
    ms=db.fetchone("SELECT * FROM message_stats WHERE guild_id=? AND user_id=?",(ctx.guild.id,ctx.author.id)); inv=db.fetchone("SELECT * FROM invite_stats WHERE guild_id=? AND user_id=?",(ctx.guild.id,ctx.author.id));
    e=discord.Embed(title=f"📊 Stats — {ctx.author.display_name}",color=discord.Color.blurple()); e.add_field(name="Messages",value=f"All-time: `{ms['total'] if ms else 0}`\nDaily: `{ms['daily'] if ms else 0}`\nWeekly: `{ms['weekly'] if ms else 0}`\nMonthly: `{ms['monthly'] if ms else 0}`",inline=False); e.add_field(name="Invites",value=f"Total: `{inv['total'] if inv else 0}`\nValid: `{inv['valid'] if inv else 0}`\nLeft: `{inv['left_count'] if inv else 0}`\nRejoins: `{inv['rejoins'] if inv else 0}`",inline=False); await ctx.send(embed=e)

@bot.hybrid_command(name="stats",description="Show another member's stats")
async def stats(ctx,member:discord.Member=None):
    member=member or ctx.author; ctx.author=ctx.author
    # Reuse mystats formatting without mutating command context.
    ms=db.fetchone("SELECT * FROM message_stats WHERE guild_id=? AND user_id=?",(ctx.guild.id,member.id)); inv=db.fetchone("SELECT * FROM invite_stats WHERE guild_id=? AND user_id=?",(ctx.guild.id,member.id));
    e=discord.Embed(title=f"📊 Stats — {member.display_name}",color=member.color if member.color.value else discord.Color.blurple()); e.set_thumbnail(url=member.display_avatar.url); e.add_field(name="Messages",value=f"All-time: `{ms['total'] if ms else 0}`\nDaily: `{ms['daily'] if ms else 0}`\nWeekly: `{ms['weekly'] if ms else 0}`\nMonthly: `{ms['monthly'] if ms else 0}`",inline=False); e.add_field(name="Invites",value=f"Total: `{inv['total'] if inv else 0}`\nValid: `{inv['valid'] if inv else 0}`\nFake: `{inv['fake'] if inv else 0}`\nLeft: `{inv['left_count'] if inv else 0}`\nRejoins: `{inv['rejoins'] if inv else 0}`",inline=False); await ctx.send(embed=e)

@bot.hybrid_command(name="leaderboard",description="Show message or invite leaderboard")
@app_commands.describe(category="messages or invites")
async def leaderboard(ctx,category:Literal["messages","invites"]="messages"):
    if category=="messages": rows=db.fetchall("SELECT user_id,total FROM message_stats WHERE guild_id=? ORDER BY total DESC LIMIT 10",(ctx.guild.id,)); text="\n".join(f"**{i}.** <@{r['user_id']}> — `{r['total']}` messages" for i,r in enumerate(rows,1))
    elif category=="invites": rows=db.fetchall("SELECT user_id,valid,total,left_count FROM invite_stats WHERE guild_id=? ORDER BY valid DESC,total DESC LIMIT 10",(ctx.guild.id,)); text="\n".join(f"**{i}.** <@{r['user_id']}> — `{r['valid']}` valid / `{r['total']}` total / `{r['left_count']}` left" for i,r in enumerate(rows,1))
    else: rows=db.fetchall("SELECT user_id,seconds FROM voice_stats WHERE guild_id=? ORDER BY seconds DESC LIMIT 10",(ctx.guild.id,)); text="\n".join(f"**{i}.** <@{r['user_id']}> — `{int(r['seconds']/60)}` minutes" for i,r in enumerate(rows,1))
    await ctx.send(embed=discord.Embed(title=f"🏆 {category.title()} Leaderboard",description=text or "No data yet.",color=discord.Color.gold()))

@bot.hybrid_command(name="invites",description="Show invite statistics")
async def invites(ctx,member:discord.Member=None):
    member=member or ctx.author; r=db.fetchone("SELECT * FROM invite_stats WHERE guild_id=? AND user_id=?",(ctx.guild.id,member.id)); r=r or {"total":0,"valid":0,"fake":0,"left_count":0,"rejoins":0}; await ctx.send(embed=discord.Embed(title=f"Invite Stats — {member.display_name}",description=f"Total: `{r['total']}`\nValid: `{r['valid']}`\nFake: `{r['fake']}`\nLeft: `{r['left_count']}`\nRejoins: `{r['rejoins']}`",color=discord.Color.blue()))

@bot.hybrid_command(name="inviteleaderboard",description="Show invite leaderboard")
async def inviteleaderboard(ctx): await leaderboard.callback(ctx,"invites")

# ============================================================
# AUTOMOD
# ============================================================
def automod_row(guild_id):
    db.execute("INSERT OR IGNORE INTO automod_rules(guild_id) VALUES(?)",(guild_id,)); return db.fetchone("SELECT * FROM automod_rules WHERE guild_id=?",(guild_id,))

def automod_violation(message,row):
    text=message.content or ""; low=text.lower()
    try: bad=json.loads(row["bad_words"])
    except Exception: bad=[]
    if any(w.lower() in low for w in bad): return "bad word"
    if row["blocked_invites"] and re.search(r"discord(?:\.gg|\.com/invite)/[A-Za-z0-9-]+",low): return "Discord invite"
    if row["blocked_links"] and re.search(r"https?://\S+",low): return "external link"
    if len(message.mentions)>=row["mention_limit"]: return "mention spam"
    letters=[c for c in text if c.isalpha()]
    if len(letters)>=8 and sum(c.isupper() for c in letters)/len(letters)*100>=row["caps_percent"]: return "caps spam"
    emojis=len(re.findall(r"<a?:\w+:\d+>|[\U0001F300-\U0001FAFF]",text))
    if emojis>=row["emoji_limit"]: return "emoji spam"
    if row["attachment_filter"] and message.attachments: return "attachments blocked"
    return None

@bot.tree.command(name="setautomod",description="Configure core AutoMod")
@app_command_check("administrator")
@app_commands.describe(blocked_invites="Block Discord invites",blocked_links="Block external links",mention_limit="Mention threshold",caps_percent="Caps percentage")
async def setautomod(interaction,blocked_invites:bool=False,blocked_links:bool=False,mention_limit:int=5,caps_percent:int=80):
    automod_row(interaction.guild.id); db.execute("UPDATE automod_rules SET blocked_invites=?,blocked_links=?,mention_limit=?,caps_percent=? WHERE guild_id=?",(int(blocked_invites),int(blocked_links),max(2,mention_limit),max(50,min(100,caps_percent)),interaction.guild.id)); await interaction.response.send_message("✅ AutoMod settings saved persistently.",ephemeral=True)

# ============================================================
# WELCOME / AUTOROLE / AFK / LOCKDOWN
# ============================================================
@bot.tree.command(name="setwelcome",description="Configure welcome system")
@app_command_check("administrator")
@app_commands.describe(channel="Welcome channel",message="Message; supports {user} {username} {server} {count} {inviter}",image="Optional image URL")
async def setwelcome(interaction,channel:discord.TextChannel,message:str="Hey {user}, welcome to **{server}**! Member count: {count}",image:str=""):
    db.guild_defaults(interaction.guild.id); db.execute("UPDATE guild_config SET welcome_channel=?,welcome_message=?,welcome_image=?,welcome_enabled=1 WHERE guild_id=?",(channel.id,message,image or None,interaction.guild.id)); await interaction.response.send_message("✅ Welcome system enabled.",ephemeral=True)

@bot.tree.command(name="setautorole",description="Set automatic member role")
@app_command_check("administrator")
async def setautorole(interaction,role:discord.Role): db.execute("UPDATE guild_config SET autorole_id=? WHERE guild_id=?",(role.id,interaction.guild.id)); await interaction.response.send_message(f"✅ Autorole set to {role.mention}.",ephemeral=True)

@bot.hybrid_command(name="afk",description="Set an AFK reason")
async def afk(ctx,reason:str="AFK"):
    db.execute("INSERT OR REPLACE INTO afk(guild_id,user_id,reason,created_at) VALUES(?,?,?,?)",(ctx.guild.id,ctx.author.id,reason,iso(utcnow()))); await ctx.send(f"💤 {ctx.author.mention} is now AFK: **{reason}**")

@bot.hybrid_command(name="lockdown",description="Lock configured server text channels")
@app_command_check("manage_channels")
@command_check("manage_channels")
async def lockdown(ctx):
    if not ctx.guild: return
    count=0
    for ch in ctx.guild.text_channels:
        try:
            ow=ch.overwrites_for(ctx.guild.default_role); ow.send_messages=False; await ch.set_permissions(ctx.guild.default_role,overwrite=ow,reason=f"Server lockdown by {ctx.author}"); count+=1
        except Exception: pass
    db.execute("UPDATE guild_config SET lockdown=1 WHERE guild_id=?",(ctx.guild.id,)); await ctx.send(f"🔒 Lockdown enabled across **{count}** text channels.")

@bot.hybrid_command(name="unlockdown",description="Restore configured server text channels")
@app_command_check("manage_channels")
@command_check("manage_channels")
async def unlockdown(ctx):
    count=0
    for ch in ctx.guild.text_channels:
        try:
            ow=ch.overwrites_for(ctx.guild.default_role); ow.send_messages=None; await ch.set_permissions(ctx.guild.default_role,overwrite=ow,reason=f"Server unlockdown by {ctx.author}"); count+=1
        except Exception: pass
    db.execute("UPDATE guild_config SET lockdown=0 WHERE guild_id=?",(ctx.guild.id,)); await ctx.send(f"🔓 Lockdown removed from **{count}** text channels.")

# ============================================================
# SUGGESTIONS / POLLS
# ============================================================
@bot.hybrid_command(name="suggest",description="Submit a server suggestion")
async def suggest(ctx,text:str):
    row=db.guild_defaults(ctx.guild.id); ch=ctx.guild.get_channel(row["suggestion_channel"] or 0) or ctx.channel
    e=discord.Embed(title="💡 New Suggestion",description=text,color=discord.Color.blurple(),timestamp=utcnow()); e.set_author(name=str(ctx.author),icon_url=ctx.author.display_avatar.url); e.set_footer(text="Status: Pending")
    msg=await ch.send(embed=e); await msg.add_reaction("👍"); await msg.add_reaction("👎"); db.execute("INSERT INTO suggestions(guild_id,channel_id,message_id,author_id,text,created_at) VALUES(?,?,?,?,?,?)",(ctx.guild.id,ch.id,msg.id,ctx.author.id,text,iso(utcnow()))); await ctx.send("✅ Suggestion submitted.",delete_after=5)

@bot.tree.command(name="set_suggestion_channel",description="Set suggestion channel")
@app_command_check("administrator")
async def set_suggestion_channel(interaction,channel:discord.TextChannel): db.execute("UPDATE guild_config SET suggestion_channel=? WHERE guild_id=?",(channel.id,interaction.guild.id)); await interaction.response.send_message(f"✅ Suggestions will use {channel.mention}.",ephemeral=True)

@bot.hybrid_command(name="poll",description="Create a simple poll")
@app_command_check("manage_messages")
@command_check("manage_messages")
async def poll(ctx,question:str,options:str):
    opts=[x.strip() for x in options.split("|") if x.strip()][:10]
    if len(opts)<2: return await ctx.send("❌ Provide at least 2 options separated by `|`.")
    nums="1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣7️⃣8️⃣9️⃣🔟"; desc="\n".join(f"{nums[i]} {x}" for i,x in enumerate(opts)); e=discord.Embed(title=f"📊 {question}",description=desc,color=discord.Color.blurple()); msg=await ctx.send(embed=e)
    for i in range(len(opts)): await msg.add_reaction(nums[i])
    db.execute("INSERT INTO polls(message_id,guild_id,channel_id,options) VALUES(?,?,?,?)",(msg.id,ctx.guild.id,ctx.channel.id,json.dumps(opts)))

# ============================================================
# INFORMATION / SERVER CONFIG
# ============================================================
@bot.hybrid_command(name="serverinfo",description="Show server information")
async def serverinfo(ctx):
    g=ctx.guild; e=discord.Embed(title=f"Server Information — {g.name}",color=discord.Color.blurple());
    if g.icon: e.set_thumbnail(url=g.icon.url)
    e.add_field(name="Owner",value=g.owner.mention if g.owner else "Unknown"); e.add_field(name="Members",value=f"{g.member_count:,}"); e.add_field(name="Channels",value=str(len(g.channels))); e.add_field(name="Roles",value=str(len(g.roles))); e.add_field(name="ID",value=str(g.id)); e.add_field(name="Created",value=discord.utils.format_dt(g.created_at,"F"),inline=False); await ctx.send(embed=e)

@bot.hybrid_command(name="roleinfo",description="Show roles")
async def roleinfo(ctx):
    roles=list(reversed(ctx.guild.roles)); text="\n".join(f"{r.mention} — `{len(r.members)}` members" for r in roles); await ctx.send(embed=discord.Embed(title=f"Roles — {ctx.guild.name}",description=text[:4096],color=discord.Color.blurple()))

@bot.hybrid_command(name="userinfo",description="Show member information")
async def userinfo(ctx,member:discord.Member=None):
    m=member or ctx.author; e=discord.Embed(title=f"User Information — {m}",color=m.color if m.color.value else discord.Color.blurple()); e.set_thumbnail(url=m.display_avatar.url); e.add_field(name="Username",value=str(m)); e.add_field(name="ID",value=str(m.id)); e.add_field(name="Bot",value=str(m.bot)); e.add_field(name="Account Created",value=discord.utils.format_dt(m.created_at,"F"),inline=False); e.add_field(name="Joined",value=discord.utils.format_dt(m.joined_at,"F") if m.joined_at else "Unknown",inline=False); e.add_field(name="Roles",value=", ".join(r.mention for r in m.roles[1:])[:1024] or "None",inline=False); await ctx.send(embed=e)

@bot.hybrid_command(name="channelinfo",description="Show channel information")
async def channelinfo(ctx,channel:discord.TextChannel=None):
    c=channel or ctx.channel; e=discord.Embed(title=f"Channel Information — #{c.name}",color=discord.Color.blurple()); e.add_field(name="ID",value=str(c.id)); e.add_field(name="Type",value=str(c.type)); e.add_field(name="Category",value=c.category.mention if c.category else "None"); e.add_field(name="Created",value=discord.utils.format_dt(c.created_at,"F"),inline=False); e.add_field(name="Topic",value=c.topic or "None",inline=False); await ctx.send(embed=e)

@bot.hybrid_command(name="avatar",description="Show a member avatar")
async def avatar(ctx,member:discord.Member=None): m=member or ctx.author; e=discord.Embed(title=f"{m.display_name}'s Avatar",color=discord.Color.blurple()); e.set_image(url=m.display_avatar.url); await ctx.send(embed=e)

@bot.hybrid_command(name="servericon",description="Show server icon")
async def servericon(ctx):
    if not ctx.guild.icon: return await ctx.send("❌ No server icon.")
    e=discord.Embed(title=f"{ctx.guild.name} — Server Icon"); e.set_image(url=ctx.guild.icon.url); await ctx.send(embed=e)

@bot.hybrid_command(name="membercount",description="Show member count")
async def membercount(ctx): await ctx.send(f"👥 **{ctx.guild.name}** has **{ctx.guild.member_count:,}** members.")

@bot.hybrid_command(name="permissions",description="Show member permissions")
async def permissions(ctx,member:discord.Member=None):
    m=member or ctx.author; perms="\n".join(f"• {n.replace('_',' ').title()}" for n,v in m.guild_permissions if v) or "None"; await ctx.send(embed=discord.Embed(title=f"Permissions — {m}",description=perms[:4096],color=discord.Color.green()))

@bot.tree.command(name="serverconfig",description="Show current persistent server configuration")
@app_command_check("administrator")
async def serverconfig(interaction):
    r=db.guild_defaults(interaction.guild.id); e=discord.Embed(title="⚙️ Server Configuration",color=discord.Color.blurple()); e.add_field(name="Prefix",value=f"`{r['prefix']}`"); e.add_field(name="Welcome",value="Enabled" if r["welcome_enabled"] else "Disabled"); e.add_field(name="Autorole",value=f"<@&{r['autorole_id']}>" if r["autorole_id"] else "Not set"); e.add_field(name="Mod Log",value=f"<#${r['modlog_channel']}>".replace("$","") if r["modlog_channel"] else "Not set"); e.add_field(name="Log",value=f"<#${r['log_channel']}>".replace("$","") if r["log_channel"] else "Not set"); await interaction.response.send_message(embed=e,ephemeral=True)

@bot.tree.command(name="setlog",description="Set general log channel")
@app_command_check("administrator")
async def setlog(interaction,channel:discord.TextChannel): db.execute("UPDATE guild_config SET log_channel=? WHERE guild_id=?",(channel.id,interaction.guild.id)); await interaction.response.send_message(f"✅ General log channel: {channel.mention}",ephemeral=True)

@bot.tree.command(name="setmodlog",description="Set moderation log channel")
@app_command_check("administrator")
async def setmodlog(interaction,channel:discord.TextChannel): db.execute("UPDATE guild_config SET modlog_channel=? WHERE guild_id=?",(channel.id,interaction.guild.id)); await interaction.response.send_message(f"✅ Mod log channel: {channel.mention}",ephemeral=True)

@bot.hybrid_command(name="purge",description="Delete 1-100 messages")
@app_command_check("manage_messages")
@command_check("manage_messages")
async def purge(ctx,amount:int):
    if not 1<=amount<=100: return await ctx.send("❌ Amount must be 1-100.",ephemeral=True)
    if ctx.interaction: await ctx.interaction.response.defer(ephemeral=True); deleted=await ctx.channel.purge(limit=amount); await ctx.interaction.followup.send(f"🧹 Deleted **{len(deleted)}** messages.",ephemeral=True)
    else: deleted=await ctx.channel.purge(limit=amount); await ctx.send(f"🧹 Deleted **{len(deleted)}** messages.",delete_after=5)

@bot.hybrid_command(name="role",description="Add/remove a role")
@app_commands.describe(action="add or remove",member="Member",role="Role")
@app_command_check("manage_roles")
@command_check("manage_roles")
async def role(ctx,action:Literal["add","remove"],member:discord.Member,role:discord.Role):
    if ctx.guild.me and role>=ctx.guild.me.top_role: return await ctx.send("❌ I cannot manage that role.")
    if ctx.author.id!=OWNER_ID and role>=ctx.author.top_role: return await ctx.send("❌ You cannot manage that role.")
    if action=="add": await member.add_roles(role,reason=f"Managed by {ctx.author}")
    else: await member.remove_roles(role,reason=f"Managed by {ctx.author}")
    await ctx.send(f"✅ Role `{role.name}` {action}ed for {member.mention}.")

# ============================================================
# EVENTS: persistent recovery, logging, stats, automod, invites
# ============================================================
@bot.event
async def on_ready():
    migrate_legacy_json()
    log.info("Logged in as %s (%s)",bot.user,bot.user.id)
    for g in bot.guilds:
        db.guild_defaults(g.id)
        try: invite_cache[g.id]=await g.invites()
        except Exception: invite_cache[g.id]=[]
    if "global" not in loaded_views:
        bot.add_view(TicketControlView()); bot.add_view(VerifyView()); loaded_views.add("global")
    for p in db.fetchall("SELECT panel_id,message_id FROM ticket_panels WHERE active=1"):
        try:
            view=TicketPanelView(p["panel_id"]); bot.add_view(view,message_id=p["message_id"] if p["message_id"] else None)
        except Exception as e: log.warning("view restore failed for %s: %s",p["panel_id"],e)
    for gid,cid,mid in RECOVERY_MESSAGES: await recover_giveaway_message(gid,cid,mid)
    if not giveaway_worker.is_running(): giveaway_worker.start()
    try:
        synced=await bot.tree.sync(); log.info("Synced %s slash commands",len(synced))
    except Exception as e: log.warning("slash sync failed: %s",e)

@tasks.loop(seconds=5)
async def giveaway_worker():
    rows=db.fetchall("SELECT * FROM giveaways WHERE ended=0 AND end_at<=?",(iso(utcnow()),))
    for row in rows:
        g=bot.get_guild(row["guild_id"]); ch=g.get_channel(row["channel_id"]) if g else None
        if not ch:
            db.execute("UPDATE giveaways SET ended=1 WHERE message_id=?",(row["message_id"],)); continue
        try: msg=await ch.fetch_message(row["message_id"]); await pick_giveaway_winners(msg,row)
        except Exception as e: log.warning("giveaway worker failed %s: %s",row["message_id"],e)

@giveaway_worker.before_loop
async def before_worker(): await bot.wait_until_ready()

@bot.event
async def on_message(message):
    if message.guild is None: return await bot.process_commands(message)
    if message.author.bot: return
    # AFK mention response / automatic AFK removal
    afk_user=db.fetchone("SELECT * FROM afk WHERE guild_id=? AND user_id=?",(message.guild.id,message.author.id))
    if afk_user:
        db.execute("DELETE FROM afk WHERE guild_id=? AND user_id=?",(message.guild.id,message.author.id)); await message.channel.send(f"👋 Welcome back {message.author.mention}! Your AFK has been removed.",delete_after=5)
    for m in message.mentions:
        row=db.fetchone("SELECT reason FROM afk WHERE guild_id=? AND user_id=?",(message.guild.id,m.id))
        if row: await message.channel.send(f"💤 {m.display_name} is AFK: **{row['reason']}**",delete_after=8)
    if not is_whitelisted(message.author,message.guild):
        ar=automod_row(message.guild.id); reason=automod_violation(message,ar)
        if reason:
            if ar["auto_delete"]:
                try: await message.delete()
                except Exception: pass
            if ar["auto_warn"]: db.execute("INSERT INTO warnings(guild_id,user_id,moderator_id,reason,created_at) VALUES(?,?,?,?,?)",(message.guild.id,message.author.id,bot.user.id,f"AutoMod: {reason}",iso(utcnow())))
            if ar["auto_timeout"]:
                try: await message.author.timeout(timedelta(minutes=5),reason=f"AutoMod: {reason}")
                except Exception: pass
            return
        # legacy anti-spam retained, now per guild/user and not lost on process logic.
        key=(message.guild.id,message.author.id); now=utcnow().timestamp(); q=spam_tracker[key]
        while q and now-q[0]>5: q.popleft()
        q.append(now)
        if len(q)>=db.guild_defaults(message.guild.id)["spam_limit"]:
            try: await message.author.timeout(timedelta(minutes=5),reason="Anti-Spam")
            except Exception: pass
            q.clear()
    increment_message(message.guild.id,message.author.id)
    new_level=_give_xp(message.guild.id,message.author.id)
    if new_level:
        lcfg=db.fetchone("SELECT * FROM level_config WHERE guild_id=?",(message.guild.id,))
        reward=db.fetchone("SELECT role_id FROM level_rewards WHERE guild_id=? AND level=?",(message.guild.id,new_level))
        if reward:
            rr=message.guild.get_role(reward["role_id"])
            if rr:
                try: await message.author.add_roles(rr,reason=f"Level {new_level} reward")
                except Exception: pass
        if lcfg and lcfg["announce_channel"]:
            lc=message.guild.get_channel(lcfg["announce_channel"])
            if lc:
                try: await lc.send(safe_format(lcfg["announce_message"].replace("{level}",str(new_level)),message.author,message.guild))
                except Exception: pass
    cfg=db.guild_defaults(message.guild.id)
    prefix=cfg['prefix']
    if message.content.startswith(prefix):
        raw=message.content[len(prefix):].strip().split(None,1)
        if raw:
            cname=raw[0].lower()
            custom=db.fetchone("SELECT response FROM custom_commands WHERE guild_id=? AND name=?",(message.guild.id,cname))
            if custom:
                await message.channel.send(safe_format(custom['response'],message.author,message.guild))
                return
    sticky_row=db.fetchone("SELECT message_id,content FROM sticky_messages WHERE channel_id=?",(message.channel.id,))
    if sticky_row and not message.content.startswith(prefix):
        try:
            old_sticky=await message.channel.fetch_message(sticky_row["message_id"]); await old_sticky.delete()
        except Exception: pass
        try:
            new_sticky=await message.channel.send(f"📌 **Sticky:**\n{sticky_row['content']}")
            db.execute("UPDATE sticky_messages SET message_id=? WHERE channel_id=?",(new_sticky.id,message.channel.id))
        except Exception: pass
    await bot.process_commands(message)

@bot.event
async def on_member_join(member):
    g=member.guild; inviter=None
    db.execute("INSERT INTO join_history(guild_id,user_id,joined_at) VALUES(?,?,?)",(g.id,member.id,iso(utcnow())))
    raid=db.fetchone("SELECT * FROM raid_config WHERE guild_id=? AND enabled=1",(g.id,))
    if raid:
        cutoff=iso(utcnow()-timedelta(seconds=raid['window_seconds']))
        recent=db.fetchone("SELECT COUNT(*) AS c FROM join_history WHERE guild_id=? AND joined_at>=?",(g.id,cutoff))
        if recent and recent['c']>=raid['join_limit']:
            try:
                await server_log(g,"🚨 Raid Protection Triggered",f"**{recent['c']} joins** detected within **{raid['window_seconds']}s**. Action: **{raid['action']}**",discord.Color.red(),"mod")
                if raid['action']=="lockdown":
                    for ch in g.text_channels:
                        if ch.permissions_for(g.default_role).send_messages is not False:
                            try: await ch.set_permissions(g.default_role,send_messages=False,reason="Raid protection lockdown")
                            except Exception: pass
                    db.set_guild(g.id,"lockdown",1)
            except Exception: pass
    try:
        old=invite_cache.get(g.id,[]); new=await g.invites(); invite_cache[g.id]=new
        for oi in old:
            ni=discord.utils.get(new,code=oi.code)
            if ni and ni.uses>oi.uses: inviter=oi.inviter; break
    except Exception: pass
    if inviter:
        prev=db.fetchone("SELECT * FROM member_invites WHERE guild_id=? AND member_id=?",(g.id,member.id))
        if prev and prev["left_at"]:
            db.execute("UPDATE member_invites SET inviter_id=?,invite_code=?,joined_at=?,left_at=NULL WHERE guild_id=? AND member_id=?",(inviter.id,next((x.code for x in new if x.inviter and x.inviter.id==inviter.id),None),iso(utcnow()),g.id,member.id))
            db.execute("INSERT INTO invite_stats(guild_id,user_id,total,valid,fake,rejoins) VALUES(?,?,?,?,?,1) ON CONFLICT(guild_id,user_id) DO UPDATE SET total=total+1,valid=valid+1,rejoins=rejoins+1",(g.id,inviter.id,1,1,0))
        else:
            db.execute("INSERT OR REPLACE INTO member_invites(guild_id,member_id,inviter_id,invite_code,joined_at) VALUES(?,?,?,?,?)",(g.id,member.id,inviter.id,next((x.code for x in new if x.inviter and x.inviter.id==inviter.id),None),iso(utcnow())))
            db.execute("INSERT INTO invite_stats(guild_id,user_id,total,valid) VALUES(?,?,1,1) ON CONFLICT(guild_id,user_id) DO UPDATE SET total=total+1,valid=valid+1",(g.id,inviter.id))
    row=db.guild_defaults(g.id)
    if row["autorole_id"]:
        r=g.get_role(row["autorole_id"])
        if r:
            try: await member.add_roles(r,reason="Autorole")
            except Exception: pass
    if row["welcome_enabled"]:
        ch=g.get_channel(row["welcome_channel"]) if row["welcome_channel"] else g.system_channel
        if ch:
            text=safe_format(row["welcome_message"] or "Hey {user}, welcome to **{server}**!",member,g,inviter.name if inviter else "Unknown")
            e=discord.Embed(title="Welcome!",description=text,color=discord.Color.green()); e.set_thumbnail(url=member.display_avatar.url)
            if row["welcome_image"]: e.set_image(url=row["welcome_image"])
            try: await ch.send(content=member.mention,embed=e)
            except Exception: pass
    await server_log(g,"📥 Member Joined",f"**Member:** {member.mention}\n**Invited By:** {inviter.mention if inviter else 'Unknown'}",discord.Color.green())

@bot.event
async def on_member_remove(member):
    row=db.fetchone("SELECT * FROM member_invites WHERE guild_id=? AND member_id=?",(member.guild.id,member.id))
    if row and row["inviter_id"]:
        db.execute("UPDATE member_invites SET left_at=? WHERE guild_id=? AND member_id=?",(iso(utcnow()),member.guild.id,member.id))
        db.execute("UPDATE invite_stats SET left_count=left_count+1 WHERE guild_id=? AND user_id=?",(member.guild.id,row["inviter_id"]))
    gcfg=db.guild_defaults(member.guild.id)
    if gcfg["goodbye_enabled"]:
        ch=member.guild.get_channel(gcfg["goodbye_channel"]) if gcfg["goodbye_channel"] else member.guild.system_channel
        if ch:
            try: await ch.send(safe_format(gcfg["goodbye_message"] or "Goodbye {username}!",member,member.guild))
            except Exception: pass
    await server_log(member.guild,"📤 Member Left",f"**Member:** {member} (`{member.id}`)",discord.Color.red())

# Basic audit-style event logs
@bot.event
async def on_message_delete(message):
    if message.guild and not message.author.bot: await server_log(message.guild,"🗑️ Message Deleted",f"**Author:** {message.author.mention}\n**Channel:** {message.channel.mention}\n**Content:** {(message.content or '[no text]')[:1500]}")
@bot.event
async def on_message_edit(before,after):
    if before.guild and not before.author.bot and before.content!=after.content: await server_log(before.guild,"✏️ Message Edited",f"**Author:** {before.author.mention}\n**Channel:** {before.channel.mention}\n**Before:** {(before.content or '[no text]')[:700]}\n**After:** {(after.content or '[no text]')[:700]}")
@bot.event
async def on_guild_channel_create(channel): await server_log(channel.guild,"📁 Channel Created",f"**Channel:** {channel.mention if hasattr(channel,'mention') else channel.name}")
@bot.event
async def on_guild_channel_delete(channel): await server_log(channel.guild,"🗑️ Channel Deleted",f"**Channel:** #{channel.name} (`{channel.id}`)")
@bot.event
async def on_guild_role_create(role): await server_log(role.guild,"🎭 Role Created",f"**Role:** {role.mention}")
@bot.event
async def on_guild_role_delete(role): await server_log(role.guild,"🗑️ Role Deleted",f"**Role:** `{role.name}` (`{role.id}`)")
@bot.event
async def on_member_update(before,after):
    if before.nick!=after.nick: await server_log(after.guild,"✏️ Nickname Changed",f"**Member:** {after.mention}\n**Before:** {before.nick or before.name}\n**After:** {after.nick or after.name}")
    if before.roles!=after.roles:
        added=[r for r in after.roles if r not in before.roles]; removed=[r for r in before.roles if r not in after.roles]
        if added or removed: await server_log(after.guild,"🎭 Roles Updated",f"**Member:** {after.mention}\n**Added:** {', '.join(r.name for r in added) or 'None'}\n**Removed:** {', '.join(r.name for r in removed) or 'None'}")

# ============================================================
# V3 ALL-ROUNDER EXTENSIONS
# ============================================================

def text_send(ctx, content, **kwargs):
    return ctx.send(content, **kwargs)

@bot.hybrid_command(name="clear", aliases=["purgeall"], description="Delete messages with optional user filter")
@app_command_check("manage_messages")
@command_check("manage_messages")
async def clear(ctx, amount:int, member:discord.Member=None):
    if amount < 1 or amount > 100: return await ctx.send("❌ Amount must be between 1 and 100.", ephemeral=True)
    deleted = await ctx.channel.purge(limit=amount, check=(lambda m: member is None or m.author.id == member.id))
    await ctx.send(f"🧹 Deleted **{len(deleted)}** message(s).", delete_after=5)
    await server_log(ctx.guild, "🧹 Messages Purged", f"**Moderator:** {ctx.author.mention}\n**Count:** {len(deleted)}\n**Channel:** {ctx.channel.mention}")

@bot.hybrid_command(name="slowmode", description="Set channel slowmode")
@app_command_check("manage_channels")
@command_check("manage_channels")
async def slowmode(ctx, seconds:int):
    if seconds < 0 or seconds > 21600: return await ctx.send("❌ Slowmode must be 0-21600 seconds.", ephemeral=True)
    await ctx.channel.edit(slowmode_delay=seconds, reason=f"Slowmode changed by {ctx.author}")
    await ctx.send(f"🐢 Slowmode set to **{seconds}s**.")

@bot.hybrid_command(name="lock", description="Lock the current channel")
@app_command_check("manage_channels")
@command_check("manage_channels")
async def lock(ctx):
    ow=ctx.channel.overwrites_for(ctx.guild.default_role); ow.send_messages=False
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=ow, reason=f"Locked by {ctx.author}")
    await ctx.send("🔒 Channel locked.")

@bot.hybrid_command(name="unlock", description="Unlock the current channel")
@app_command_check("manage_channels")
@command_check("manage_channels")
async def unlock(ctx):
    ow=ctx.channel.overwrites_for(ctx.guild.default_role); ow.send_messages=None
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=ow, reason=f"Unlocked by {ctx.author}")
    await ctx.send("🔓 Channel unlocked.")

@bot.hybrid_command(name="tempban", description="Temporarily ban a member")
@app_command_check("ban_members")
@command_check("ban_members")
async def tempban(ctx, member:discord.Member, duration:str, reason:str="No reason provided"):
    try: seconds=parse_time(duration)
    except ValueError as e: return await ctx.send(f"❌ {e}")
    await apply_mod(ctx, member, "tempban", reason, lambda: member.ban(reason=reason))
    async def unban_later():
        await asyncio.sleep(seconds)
        try:
            user=await bot.fetch_user(member.id); await ctx.guild.unban(user, reason="Temporary ban expired")
        except Exception: pass
    asyncio.create_task(unban_later())

@bot.hybrid_command(name="timeout", aliases=["tempmute"], description="Timeout a member")
@app_command_check("moderate_members")
@command_check("moderate_members")
async def timeout_cmd(ctx, member:discord.Member, duration:str, reason:str="No reason provided"):
    return await mute.callback(ctx, member, duration, reason)

@bot.hybrid_command(name="roleinfo2", description="Detailed role information")
async def roleinfo2(ctx, role:discord.Role):
    e=discord.Embed(title=f"🎭 Role — {role.name}", color=role.color if role.color.value else discord.Color.blurple())
    e.add_field(name="ID",value=str(role.id)); e.add_field(name="Members",value=f"{len(role.members):,}")
    e.add_field(name="Position",value=str(role.position)); e.add_field(name="Mentionable",value=str(role.mentionable)); e.add_field(name="Hoisted",value=str(role.hoist)); e.add_field(name="Managed",value=str(role.managed))
    await ctx.send(embed=e)

@bot.hybrid_command(name="emojiinfo", description="Show emoji information")
async def emojiinfo(ctx, emoji:str):
    m=re.search(r"<(a?):([A-Za-z0-9_]+):(\d+)>",emoji)
    if not m: return await ctx.send("❌ Please provide a custom Discord emoji.")
    animated,name,eid=m.groups(); e=discord.Embed(title=f"😀 Emoji — {name}",color=discord.Color.blurple()); e.add_field(name="ID",value=eid); e.add_field(name="Animated",value="Yes" if animated else "No"); e.set_image(url=f"https://cdn.discordapp.com/emojis/{eid}.{'gif' if animated else 'png'}?size=256"); await ctx.send(embed=e)

@bot.tree.command(name="setprefix", description="Set this server's bot prefix")
@app_command_check("administrator")
async def setprefix(interaction, prefix:str):
    prefix=prefix.strip()
    if not 1<=len(prefix)<=5 or prefix.isspace(): return await interaction.response.send_message("❌ Prefix must be 1-5 characters.",ephemeral=True)
    db.set_guild(interaction.guild.id,"prefix",prefix); await interaction.response.send_message(f"✅ Prefix changed to `{prefix}`.",ephemeral=True)

@bot.hybrid_command(name="prefix", description="Show this server's prefix")
async def prefix(ctx):
    r=db.guild_defaults(ctx.guild.id); await ctx.send(f"⚙️ Current prefix: `{r['prefix']}`")

@bot.tree.command(name="setgoodbye", description="Configure goodbye messages")
@app_command_check("administrator")
async def setgoodbye(interaction, channel:discord.TextChannel, message:str="Goodbye {username}!", image:str=""):
    db.execute("UPDATE guild_config SET welcome_channel=?,goodbye_message=?,goodbye_image=?,goodbye_enabled=1 WHERE guild_id=?",(channel.id,message,image or None,interaction.guild.id))
    await interaction.response.send_message("✅ Goodbye system enabled.",ephemeral=True)

@bot.tree.command(name="disablegoodbye", description="Disable goodbye messages")
@app_command_check("administrator")
async def disablegoodbye(interaction):
    db.execute("UPDATE guild_config SET goodbye_enabled=0 WHERE guild_id=?",(interaction.guild.id,)); await interaction.response.send_message("✅ Goodbye system disabled.",ephemeral=True)

@bot.tree.command(name="set_ticket_log", description="Set ticket transcript/log channel")
@app_command_check("administrator")
async def set_ticket_log(interaction, channel:discord.TextChannel):
    db.execute("UPDATE guild_config SET ticket_log_channel=? WHERE guild_id=?",(channel.id,interaction.guild.id)); await interaction.response.send_message(f"✅ Ticket logs: {channel.mention}",ephemeral=True)

@bot.tree.command(name="set_invite_log", description="Set invite log channel")
@app_command_check("administrator")
async def set_invite_log(interaction, channel:discord.TextChannel):
    db.execute("UPDATE guild_config SET invite_log_channel=? WHERE guild_id=?",(channel.id,interaction.guild.id)); await interaction.response.send_message(f"✅ Invite logs: {channel.mention}",ephemeral=True)

@bot.hybrid_command(name="botinfo", description="Show bot information")
async def botinfo(ctx):
    e=discord.Embed(title="🤖 Bot Information",color=discord.Color.blurple()); e.set_thumbnail(url=bot.user.display_avatar.url)
    e.add_field(name="Bot",value=str(bot.user)); e.add_field(name="ID",value=str(bot.user.id)); e.add_field(name="Servers",value=f"{len(bot.guilds):,}"); e.add_field(name="Users",value=f"{sum(g.member_count or 0 for g in bot.guilds):,}"); e.add_field(name="Prefix",value=f"`{db.guild_defaults(ctx.guild.id)['prefix']}`"); e.add_field(name="Latency",value=f"{round(bot.latency*1000)}ms")
    await ctx.send(embed=e)

@bot.hybrid_command(name="case", description="View one moderation case")
async def case(ctx, case_id:int):
    r=db.fetchone("SELECT * FROM mod_cases WHERE guild_id=? AND case_id=?",(ctx.guild.id,case_id))
    if not r: return await ctx.send("❌ Case not found.")
    e=discord.Embed(title=f"Case #{case_id}",color=discord.Color.blurple()); e.add_field(name="Action",value=r['action']); e.add_field(name="User",value=f"<@{r['user_id']}> "); e.add_field(name="Moderator",value=f"<@{r['moderator_id']}> "); e.add_field(name="Reason",value=r['reason'][:1024],inline=False); e.set_footer(text=r['created_at']); await ctx.send(embed=e)

@bot.hybrid_command(name="removewarn", description="Remove one active warning")
@app_command_check("moderate_members")
@command_check("moderate_members")
async def removewarn(ctx, warning_id:int):
    r=db.fetchone("SELECT * FROM warnings WHERE id=? AND guild_id=? AND active=1",(warning_id,ctx.guild.id))
    if not r: return await ctx.send("❌ Active warning not found.")
    db.execute("UPDATE warnings SET active=0 WHERE id=?",(warning_id,)); await ctx.send(f"✅ Warning #{warning_id} removed.")

@bot.tree.command(name="verify_setup", description="Set verification channel and role")
@app_command_check("administrator")
async def verify_setup(interaction, channel:discord.TextChannel, role:discord.Role):
    db.execute("INSERT INTO verification_config(guild_id,channel_id,role_id,enabled) VALUES(?,?,?,1) ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id,role_id=excluded.role_id,enabled=1",(interaction.guild.id,channel.id,role.id)); await interaction.response.send_message(f"✅ Verification configured: {channel.mention} → {role.mention}",ephemeral=True)

class VerifyView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Verify",emoji="✅",style=discord.ButtonStyle.success,custom_id="allrounder:verify")
    async def verify(self,interaction,button):
        row=db.fetchone("SELECT * FROM verification_config WHERE guild_id=? AND enabled=1",(interaction.guild.id,))
        if not row: return await interaction.response.send_message("❌ Verification is not configured.",ephemeral=True)
        role=interaction.guild.get_role(row['role_id'])
        if not role: return await interaction.response.send_message("❌ Verification role no longer exists.",ephemeral=True)
        try: await interaction.user.add_roles(role,reason="Verification")
        except discord.Forbidden: return await interaction.response.send_message("❌ I cannot assign the verification role. Move my role above it.",ephemeral=True)
        await interaction.response.send_message("✅ You are verified.",ephemeral=True)

@bot.tree.command(name="verify_panel", description="Post the verification panel")
@app_command_check("administrator")
async def verify_panel(interaction):
    row=db.fetchone("SELECT * FROM verification_config WHERE guild_id=? AND enabled=1",(interaction.guild.id,))
    if not row: return await interaction.response.send_message("❌ Run `/verify_setup` first.",ephemeral=True)
    e=discord.Embed(title="🛡️ Verification",description="Click **Verify** to receive the verified role.",color=discord.Color.green()); await interaction.response.send_message(embed=e,view=VerifyView())
    bot.add_view(VerifyView())

@bot.tree.command(name="set_reaction_role", description="Bind a reaction to a role")
@app_command_check("administrator")
async def set_reaction_role(interaction, message_id:str, emoji:str, role:discord.Role):
    try: mid=int(message_id)
    except ValueError: return await interaction.response.send_message("❌ Invalid message ID.",ephemeral=True)
    db.execute("INSERT OR REPLACE INTO reaction_roles_v3(guild_id,message_id,emoji,role_id) VALUES(?,?,?,?)",(interaction.guild.id,mid,emoji,role.id)); await interaction.response.send_message("✅ Reaction role saved. Add the same reaction to the message.",ephemeral=True)

@bot.tree.command(name="remove_reaction_role", description="Remove a reaction-role binding")
@app_command_check("administrator")
async def remove_reaction_role(interaction, message_id:str, emoji:str):
    try: mid=int(message_id)
    except ValueError: return await interaction.response.send_message("❌ Invalid message ID.",ephemeral=True)
    db.execute("DELETE FROM reaction_roles_v3 WHERE guild_id=? AND message_id=? AND emoji=?",(interaction.guild.id,mid,emoji)); await interaction.response.send_message("✅ Reaction role binding removed.",ephemeral=True)

@bot.tree.command(name="raid_protection_basic", description="Enable/disable simple join-burst protection")
@app_command_check("administrator")
async def raid_protection(interaction, enabled:bool=True, joins:int=8, window:int=20):
    joins=max(2,min(50,joins)); window=max(5,min(300,window)); db.execute("INSERT INTO raid_config(guild_id,join_limit,window_seconds,enabled) VALUES(?,?,?,?) ON CONFLICT(guild_id) DO UPDATE SET join_limit=excluded.join_limit,window_seconds=excluded.window_seconds,enabled=excluded.enabled",(interaction.guild.id,joins,window,int(enabled))); await interaction.response.send_message(f"✅ Raid protection {'enabled' if enabled else 'disabled'}: {joins} joins / {window}s.",ephemeral=True)

@bot.tree.command(name="custom_command", description="Create/update a simple custom command")
@app_command_check("administrator")
async def custom_command(interaction, name:str, response:str):
    name=re.sub(r"[^a-zA-Z0-9_-]","",name.lower())[:32]
    if not name: return await interaction.response.send_message("❌ Invalid command name.",ephemeral=True)
    db.execute("INSERT OR REPLACE INTO custom_commands(guild_id,name,response) VALUES(?,?,?)",(interaction.guild.id,name,response[:1900])); await interaction.response.send_message(f"✅ Custom command `{name}` saved. Use `{db.guild_defaults(interaction.guild.id)['prefix']}{name}`.",ephemeral=True)

@bot.tree.command(name="delete_custom_command", description="Delete a custom command")
@app_command_check("administrator")
async def delete_custom_command(interaction, name:str):
    db.execute("DELETE FROM custom_commands WHERE guild_id=? AND name=?",(interaction.guild.id,name.lower())); await interaction.response.send_message("✅ Custom command removed.",ephemeral=True)

@bot.event
async def on_raw_reaction_add(payload):
    if payload.guild_id is None or payload.user_id == bot.user.id: return
    emoji=str(payload.emoji)
    rows=db.fetchall("SELECT role_id FROM reaction_roles_v3 WHERE guild_id=? AND message_id=? AND emoji=?",(payload.guild_id,payload.message_id,emoji))
    guild=bot.get_guild(payload.guild_id); member=guild.get_member(payload.user_id) if guild else None
    if member:
        for row in rows:
            role=guild.get_role(row['role_id'])
            if role:
                try: await member.add_roles(role,reason="Reaction role")
                except Exception: pass

@bot.event
async def on_raw_reaction_remove(payload):
    if payload.guild_id is None: return
    emoji=str(payload.emoji); rows=db.fetchall("SELECT role_id FROM reaction_roles_v3 WHERE guild_id=? AND message_id=? AND emoji=?",(payload.guild_id,payload.message_id,emoji)); guild=bot.get_guild(payload.guild_id); member=guild.get_member(payload.user_id) if guild else None
    if member:
        for row in rows:
            role=guild.get_role(row['role_id'])
            if role:
                try: await member.remove_roles(role,reason="Reaction role removed")
                except Exception: pass


# ============================================================
# V4 ALL-ROUNDER SYSTEMS
# ============================================================

def _duration_seconds(value: str) -> int:
    return parse_time(value)

def _xp_needed(level: int) -> int:
    return 100 + (level * 50)

def _give_xp(guild_id, user_id):
    cfg=db.fetchone("SELECT * FROM level_config WHERE guild_id=?",(guild_id,))
    if cfg and not cfg["enabled"]: return None
    if not cfg:
        db.execute("INSERT OR IGNORE INTO level_config(guild_id) VALUES(?)",(guild_id,))
        cfg=db.fetchone("SELECT * FROM level_config WHERE guild_id=?",(guild_id,))
    key=(guild_id,user_id); now=utcnow().timestamp()
    if now-xp_cooldowns.get(key,0)<cfg["cooldown"]: return None
    xp_cooldowns[key]=now
    import random as _r
    gain=_r.randint(cfg["xp_min"],cfg["xp_max"])
    row=db.fetchone("SELECT xp,level FROM message_stats WHERE guild_id=? AND user_id=?",(guild_id,user_id))
    if not row:
        db.execute("INSERT INTO message_stats(guild_id,user_id,xp,level) VALUES(?,?,?,0)",(guild_id,user_id,gain)); return None
    xp=row["xp"]+gain; level=row["level"]; old=level
    while xp>=_xp_needed(level): xp-=_xp_needed(level); level+=1
    db.execute("UPDATE message_stats SET xp=?,level=? WHERE guild_id=? AND user_id=?",(xp,level,guild_id,user_id))
    return level if level>old else None

@bot.hybrid_command(name="level",description="Show your or another member's level")
async def level_cmd(ctx,member:discord.Member=None):
    m=member or ctx.author; row=db.fetchone("SELECT xp,level FROM message_stats WHERE guild_id=? AND user_id=?",(ctx.guild.id,m.id))
    xp=row["xp"] if row else 0; lvl=row["level"] if row else 0
    await ctx.send(f"🏆 **{m.display_name}** — Level **{lvl}** • XP **{xp}/{_xp_needed(lvl)}**")

@bot.hybrid_command(name="levelboard",aliases=["xpleaderboard"],description="Show the server XP leaderboard")
async def levelboard(ctx):
    rows=db.fetchall("SELECT user_id,level,xp FROM message_stats WHERE guild_id=? ORDER BY level DESC,xp DESC LIMIT 10",(ctx.guild.id,))
    lines=[]
    for i,r in enumerate(rows,1): lines.append(f"**{i}.** <@{r['user_id']}> — Level {r['level']} ({r['xp']} XP)")
    await ctx.send(embed=discord.Embed(title="🏆 Level Leaderboard",description="\n".join(lines) or "No XP yet.",color=discord.Color.gold()))

@bot.tree.command(name="level_config",description="Configure XP/leveling")
@app_command_check("administrator")
async def level_config(interaction,enabled:bool=True,xp_min:int=10,xp_max:int=20,cooldown:int=60,channel:discord.TextChannel=None):
    if xp_min<1 or xp_max<xp_min or cooldown<0: return await interaction.response.send_message("❌ Invalid XP settings.",ephemeral=True)
    db.execute("INSERT INTO level_config(guild_id,enabled,xp_min,xp_max,cooldown,announce_channel) VALUES(?,?,?,?,?,?) ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled,xp_min=excluded.xp_min,xp_max=excluded.xp_max,cooldown=excluded.cooldown,announce_channel=excluded.announce_channel",(interaction.guild.id,int(enabled),xp_min,xp_max,cooldown,channel.id if channel else None))
    await interaction.response.send_message("✅ Level system configured.",ephemeral=True)

@bot.tree.command(name="level_reward",description="Set a role reward for a level")
@app_command_check("administrator")
async def level_reward(interaction,level:int,role:discord.Role):
    if level<1: return await interaction.response.send_message("❌ Level must be 1 or higher.",ephemeral=True)
    db.execute("INSERT OR REPLACE INTO level_rewards(guild_id,level,role_id) VALUES(?,?,?)",(interaction.guild.id,level,role.id)); await interaction.response.send_message(f"✅ Level {level} reward set to {role.mention}.",ephemeral=True)

@bot.hybrid_command(name="balance",aliases=["bal"],description="Show your server balance")
async def balance(ctx,member:discord.Member=None):
    m=member or ctx.author; r=db.fetchone("SELECT balance,bank FROM economy WHERE guild_id=? AND user_id=?",(ctx.guild.id,m.id)); b=r["balance"] if r else 0; bank=r["bank"] if r else 0
    await ctx.send(f"💰 **{m.display_name}** — Wallet: **{b:,}** • Bank: **{bank:,}**")

def _econ_row(gid,uid):
    db.execute("INSERT OR IGNORE INTO economy(guild_id,user_id) VALUES(?,?)",(gid,uid)); return db.fetchone("SELECT * FROM economy WHERE guild_id=? AND user_id=?",(gid,uid))

@bot.hybrid_command(name="daily",description="Claim your daily coins")
async def daily(ctx):
    r=_econ_row(ctx.guild.id,ctx.author.id); now=utcnow(); last=parse_iso(r["last_daily"]) if r["last_daily"] else None
    if last and (now-last).total_seconds()<86400: return await ctx.send(f"⏳ Daily already claimed. Try again in {int((86400-(now-last).total_seconds())//3600)}h.")
    amount=random.randint(100,250); db.execute("UPDATE economy SET balance=balance+?,last_daily=? WHERE guild_id=? AND user_id=?",(amount,iso(now),ctx.guild.id,ctx.author.id)); await ctx.send(f"🎁 You received **{amount:,}** coins!")

@bot.hybrid_command(name="work",description="Work for coins")
async def work(ctx):
    r=_econ_row(ctx.guild.id,ctx.author.id); now=utcnow(); last=parse_iso(r["last_work"]) if r["last_work"] else None
    if last and (now-last).total_seconds()<3600: return await ctx.send("⏳ You can work again in under an hour.")
    amount=random.randint(50,180); db.execute("UPDATE economy SET balance=balance+?,last_work=? WHERE guild_id=? AND user_id=?",(amount,iso(now),ctx.guild.id,ctx.author.id)); await ctx.send(f"💼 You earned **{amount:,}** coins!")

@bot.hybrid_command(name="deposit",aliases=["dep"],description="Deposit wallet coins into bank")
async def deposit(ctx,amount:str):
    r=_econ_row(ctx.guild.id,ctx.author.id); n=r["balance"] if amount.lower()=="all" else int(amount)
    if n<=0 or n>r["balance"]: return await ctx.send("❌ Invalid amount.")
    db.execute("UPDATE economy SET balance=balance-?,bank=bank+? WHERE guild_id=? AND user_id=?",(n,n,ctx.guild.id,ctx.author.id)); await ctx.send(f"🏦 Deposited **{n:,}** coins.")

@bot.hybrid_command(name="withdraw",aliases=["with"],description="Withdraw bank coins")
async def withdraw(ctx,amount:str):
    r=_econ_row(ctx.guild.id,ctx.author.id); n=r["bank"] if amount.lower()=="all" else int(amount)
    if n<=0 or n>r["bank"]: return await ctx.send("❌ Invalid amount.")
    db.execute("UPDATE economy SET balance=balance+?,bank=bank-? WHERE guild_id=? AND user_id=?",(n,n,ctx.guild.id,ctx.author.id)); await ctx.send(f"💵 Withdrew **{n:,}** coins.")

@bot.hybrid_command(name="pay",description="Pay another member")
async def pay(ctx,member:discord.Member,amount:int):
    if member.bot or member==ctx.author or amount<=0: return await ctx.send("❌ Invalid recipient or amount.")
    r=_econ_row(ctx.guild.id,ctx.author.id)
    if r["balance"]<amount: return await ctx.send("❌ Not enough wallet balance.")
    _econ_row(ctx.guild.id,member.id); db.execute("UPDATE economy SET balance=balance-? WHERE guild_id=? AND user_id=?",(amount,ctx.guild.id,ctx.author.id)); db.execute("UPDATE economy SET balance=balance+? WHERE guild_id=? AND user_id=?",(amount,ctx.guild.id,member.id)); await ctx.send(f"💸 {ctx.author.mention} paid {member.mention} **{amount:,}** coins.")

@bot.hybrid_command(name="economyboard",aliases=["richlist"],description="Show richest members")
async def economyboard(ctx):
    rows=db.fetchall("SELECT user_id,balance,bank,(balance+bank) total FROM economy WHERE guild_id=? ORDER BY total DESC LIMIT 10",(ctx.guild.id,)); lines=[f"**{i}.** <@{r['user_id']}> — {r['total']:,}" for i,r in enumerate(rows,1)]
    await ctx.send(embed=discord.Embed(title="💰 Economy Leaderboard",description="\n".join(lines) or "No economy data yet.",color=discord.Color.green()))

@bot.hybrid_command(name="remind",description="Create a reminder")
async def remind(ctx,when:str,text:str):
    try: seconds=parse_time(when)
    except ValueError as e: return await ctx.send(f"❌ {e}")
    if seconds<5 or seconds>31536000: return await ctx.send("❌ Reminder must be between 5 seconds and 365 days.")
    at=utcnow()+timedelta(seconds=seconds); db.execute("INSERT INTO reminders(guild_id,user_id,channel_id,remind_at,text) VALUES(?,?,?,?,?)",(ctx.guild.id,ctx.author.id,ctx.channel.id,iso(at),text)); await ctx.send(f"⏰ Reminder set for <t:{int(at.timestamp())}:R>.")

@tasks.loop(seconds=10)
async def reminder_worker():
    rows=db.fetchall("SELECT * FROM reminders WHERE sent=0 AND remind_at<=? LIMIT 50",(iso(utcnow()),))
    for r in rows:
        ch=bot.get_channel(r["channel_id"]); db.execute("UPDATE reminders SET sent=1 WHERE id=?",(r["id"],))
        if ch:
            try: await ch.send(f"⏰ <@{r['user_id']}> reminder: **{r['text']}**")
            except Exception: pass

@reminder_worker.before_loop
async def before_reminder_worker(): await bot.wait_until_ready()

@bot.command(name="rolecreate",description="Create a role")
@command_check("manage_roles")
@app_command_check("manage_roles")
async def rolecreate(ctx,name:str,color:str="0x5865F2"):
    try: c=discord.Color(int(color.replace("#","").replace("0x",""),16))
    except Exception: c=discord.Color.blurple()
    r=await ctx.guild.create_role(name=name,color=c,reason=f"Created by {ctx.author}"); await ctx.send(f"✅ Created {r.mention}.")

@bot.command(name="roledelete",description="Delete a role")
@command_check("manage_roles")
@app_command_check("manage_roles")
async def roledelete(ctx,role:discord.Role):
    if ctx.guild.me and role>=ctx.guild.me.top_role: return await ctx.send("❌ I cannot delete that role.")
    await role.delete(reason=f"Deleted by {ctx.author}"); await ctx.send("✅ Role deleted.")

@bot.command(name="rolecolor",description="Change role color")
@command_check("manage_roles")
@app_command_check("manage_roles")
async def rolecolor(ctx,role:discord.Role,color:str):
    try: c=discord.Color(int(color.replace("#","").replace("0x",""),16))
    except Exception: return await ctx.send("❌ Use a hex color like #ff0000.")
    await role.edit(color=c,reason=f"Changed by {ctx.author}"); await ctx.send("✅ Role color updated.")

@bot.command(name="rolelist",description="List server roles")
async def rolelist(ctx):
    roles=[r for r in reversed(ctx.guild.roles) if r.name!="@everyone"]
    await ctx.send(embed=discord.Embed(title="🎭 Server Roles",description="\n".join(f"{r.mention} — {len(r.members)} members" for r in roles[:50]) or "No roles.",color=discord.Color.blurple()))

@bot.hybrid_command(name="channelcreate",description="Create a text channel")
@command_check("manage_channels")
@app_command_check("manage_channels")
async def channelcreate(ctx,name:str,category:discord.CategoryChannel=None):
    ch=await ctx.guild.create_text_channel(name,category=category,reason=f"Created by {ctx.author}"); await ctx.send(f"✅ Created {ch.mention}.")

@bot.hybrid_command(name="channeldelete",description="Delete a text channel")
@command_check("manage_channels")
@app_command_check("manage_channels")
async def channeldelete(ctx,channel:discord.TextChannel=None):
    ch=channel or ctx.channel; await ch.delete(reason=f"Deleted by {ctx.author}")

@bot.hybrid_command(name="renamechannel",description="Rename a text channel")
@command_check("manage_channels")
@app_command_check("manage_channels")
async def renamechannel(ctx,name:str,channel:discord.TextChannel=None):
    ch=channel or ctx.channel; await ch.edit(name=name,reason=f"Renamed by {ctx.author}"); await ctx.send(f"✅ Renamed to `{ch.name}`.")

@bot.hybrid_command(name="servericonset",description="Set server icon from an image URL")
@command_check("manage_guild")
@app_command_check("manage_guild")
async def servericonset(ctx,image_url:str):
    import urllib.request
    try:
        data=urllib.request.urlopen(image_url,timeout=10).read(); await ctx.guild.edit(icon=data,reason=f"Changed by {ctx.author}"); await ctx.send("✅ Server icon updated.")
    except Exception as e: await ctx.send(f"❌ Could not update server icon: {e}")

@bot.hybrid_command(name="memberrole",description="Add or remove a role")
@command_check("manage_roles")
@app_command_check("manage_roles")
async def memberrole(ctx,action:Literal["add","remove"],member:discord.Member,role:discord.Role):
    err=hierarchy_error(ctx,member)
    if err: return await ctx.send(err)
    if ctx.guild.me and role>=ctx.guild.me.top_role: return await ctx.send("❌ That role is above my highest role.")
    if action=="add": await member.add_roles(role,reason=f"Role command by {ctx.author}")
    else: await member.remove_roles(role,reason=f"Role command by {ctx.author}")
    await ctx.send(f"✅ Role {action}: {role.mention} → {member.mention}")

@bot.tree.command(name="ticket_edit",description="Edit the saved ticket panel")
@app_command_check("administrator")
async def ticket_edit(interaction,title:str=None,description:str=None,ticket_title:str=None,footer:str=None):
    p=panel_row(interaction.guild.id)
    if not p: return await interaction.response.send_message("❌ No ticket panel configured.",ephemeral=True)
    vals=[]; sets=[]
    for col,val in (("title",title),("description",description),("ticket_title",ticket_title),("footer",footer)):
        if val is not None: sets.append(f"{col}=?"); vals.append(val)
    if not sets: return await interaction.response.send_message("❌ Provide at least one field to edit.",ephemeral=True)
    vals.append(p["panel_id"]); db.execute(f"UPDATE ticket_panels SET {','.join(sets)} WHERE panel_id=?",vals)
    await interaction.response.send_message("✅ Ticket panel settings updated. Redeploy the panel to post the new version.",ephemeral=True)

@bot.tree.command(name="ticket_panel_edit",description="Edit ticket panel metadata")
@app_command_check("administrator")
async def ticket_panel_edit(interaction,panel_id:str,title:str,description:str):
    p=panel_row(interaction.guild.id,panel_id)
    if not p: return await interaction.response.send_message("❌ Panel not found.",ephemeral=True)
    db.execute("UPDATE ticket_panels SET title=?,description=? WHERE panel_id=?",(title,description,panel_id)); await interaction.response.send_message("✅ Ticket panel updated.",ephemeral=True)

@bot.tree.command(name="reaction_panel",description="Create a reaction-role panel message")
@app_command_check("administrator")
async def reaction_panel(interaction,title:str,description:str):
    e=discord.Embed(title=title,description=description,color=discord.Color.blurple()); await interaction.response.send_message(embed=e); m=await interaction.original_response(); db.execute("INSERT OR REPLACE INTO reaction_role_panels(guild_id,message_id,title,description) VALUES(?,?,?,?)",(interaction.guild.id,m.id,title,description)); await interaction.followup.send(f"Panel created. Use `/set_reaction_role` on message `{m.id}`.",ephemeral=True)

@bot.tree.command(name="raid_protection",description="Configure automatic raid protection")
@app_command_check("administrator")
async def raid_protection_v4(interaction,enabled:bool=True,joins:int=8,window:int=20,action:Literal["lockdown","log"]="lockdown"):
    if joins<2 or window<5: return await interaction.response.send_message("❌ Joins must be >=2 and window >=5 seconds.",ephemeral=True)
    db.execute("INSERT INTO raid_config(guild_id,join_limit,window_seconds,enabled,action) VALUES(?,?,?,?,?) ON CONFLICT(guild_id) DO UPDATE SET join_limit=excluded.join_limit,window_seconds=excluded.window_seconds,enabled=excluded.enabled,action=excluded.action",(interaction.guild.id,joins,window,int(enabled),action)); await interaction.response.send_message(f"✅ Raid protection {'enabled' if enabled else 'disabled'}: {joins} joins/{window}s, action={action}.",ephemeral=True)

@bot.hybrid_command(name="automod_status",description="Show AutoMod configuration")
@command_check("manage_guild")
@app_command_check("manage_guild")
async def automod_status(ctx):
    r=automod_row(ctx.guild.id); await ctx.send(embed=discord.Embed(title="🛡️ AutoMod",description=f"Links: {'ON' if r['blocked_links'] else 'OFF'}\nInvites: {'ON' if r['blocked_invites'] else 'OFF'}\nMention limit: {r['mention_limit']}\nCaps: {r['caps_percent']}%\nAuto delete: {'ON' if r['auto_delete'] else 'OFF'}\nAuto warn: {'ON' if r['auto_warn'] else 'OFF'}\nAuto timeout: {'ON' if r['auto_timeout'] else 'OFF'}",color=discord.Color.orange()))

@bot.hybrid_command(name="invite_sync",description="Refresh invite cache")
@command_check("manage_guild")
@app_command_check("manage_guild")
async def invite_sync(ctx):
    try: invite_cache[ctx.guild.id]=await ctx.guild.invites(); await ctx.send("✅ Invite cache refreshed.")
    except discord.Forbidden: await ctx.send("❌ I need Manage Server to read invites.")

@bot.hybrid_command(name="userstats",description="Show detailed member stats")
async def userstats(ctx,member:discord.Member=None):
    m=member or ctx.author; ms=db.fetchone("SELECT * FROM message_stats WHERE guild_id=? AND user_id=?",(ctx.guild.id,m.id)); iv=db.fetchone("SELECT * FROM invite_stats WHERE guild_id=? AND user_id=?",(ctx.guild.id,m.id));
    desc=f"Messages: **{ms['total'] if ms else 0}**\nXP: **{ms['xp'] if ms else 0}** • Level: **{ms['level'] if ms else 0}**\nInvites: **{iv['valid'] if iv else 0}** valid • **{iv['fake'] if iv else 0}** fake • **{iv['left_count'] if iv else 0}** left • **{iv['rejoins'] if iv else 0}** rejoins\nVoice: **{int((ve['seconds'] if ve else 0)/60)} min**"; await ctx.send(embed=discord.Embed(title=f"📊 {m.display_name} Stats",description=desc,color=discord.Color.blurple()))

@bot.tree.command(name="server_roles",description="Show server role statistics")
async def server_roles(interaction):
    roles=[r for r in interaction.guild.roles if r.name!="@everyone"]; await interaction.response.send_message(embed=discord.Embed(title="🎭 Role Statistics",description="\n".join(f"{r.mention}: {len(r.members)}" for r in reversed(roles[:50])) or "No roles."))

@bot.event
async def on_member_ban(guild,user): await server_log(guild,"🔨 Member Banned",f"**User:** {user} (`{user.id}`)")

@bot.event
async def on_member_unban(guild,user): await server_log(guild,"🔓 Member Unbanned",f"**User:** {user} (`{user.id}`)")

@bot.event
async def on_guild_channel_update(before,after):
    if before.name!=after.name: await server_log(after.guild,"✏️ Channel Updated",f"**Before:** {before.name}\n**After:** {after.name}")

@bot.event
async def on_guild_role_update(before,after):
    if before.name!=after.name or before.permissions!=after.permissions: await server_log(after.guild,"✏️ Role Updated",f"**Role:** {after.mention}\n**Before:** {before.name}\n**After:** {after.name}")

@bot.event
async def on_guild_update(before,after):
    changes=[]
    if before.name!=after.name: changes.append(f"Name: `{before.name}` → `{after.name}`")
    if changes: await server_log(after,"🏠 Server Updated","\n".join(changes))

# Start the persistent reminder worker alongside the giveaway worker.
_original_on_ready = on_ready

# Patch the existing on_ready body by using a lightweight background starter event hook.
@bot.listen("on_ready")
async def _v4_start_workers():
    if not reminder_worker.is_running(): reminder_worker.start()



@bot.tree.command(name="starboard",description="Configure the starboard")
@app_command_check("administrator")
async def starboard(interaction,enabled:bool=True,channel:discord.TextChannel=None,threshold:int=5,emoji:str="⭐"):
    if threshold<1: return await interaction.response.send_message("❌ Threshold must be at least 1.",ephemeral=True)
    db.execute("INSERT INTO starboard_config(guild_id,channel_id,emoji,threshold,enabled) VALUES(?,?,?,?,?) ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id,emoji=excluded.emoji,threshold=excluded.threshold,enabled=excluded.enabled",(interaction.guild.id,channel.id if channel else None,emoji,threshold,int(enabled)))
    await interaction.response.send_message(f"⭐ Starboard {'enabled' if enabled else 'disabled'}.",ephemeral=True)

@bot.listen("on_raw_reaction_add")
async def on_raw_reaction_add_starboard(payload):
    if payload.guild_id is None or payload.user_id == bot.user.id: return
    cfg=db.fetchone("SELECT * FROM starboard_config WHERE guild_id=? AND enabled=1",(payload.guild_id,))
    if not cfg or str(payload.emoji)!=cfg["emoji"]: return
    guild=bot.get_guild(payload.guild_id); channel=guild.get_channel(cfg["channel_id"]) if guild and cfg["channel_id"] else None
    if not channel: return
    source=guild.get_channel(payload.channel_id)
    if not source: return
    try: msg=await source.fetch_message(payload.message_id)
    except Exception: return
    reaction=discord.utils.find(lambda r:str(r.emoji)==cfg["emoji"],msg.reactions)
    if not reaction or reaction.count < cfg["threshold"]: return
    old=db.fetchone("SELECT starboard_message_id FROM starboard_posts WHERE guild_id=? AND message_id=?",(guild.id,msg.id))
    text=f"{cfg['emoji']} **{reaction.count}** • {msg.author.mention}\n{msg.content[:1800] or '[embed/attachment only]'}\n[Jump to message]({msg.jump_url})"
    try:
        if old:
            sm=await channel.fetch_message(old["starboard_message_id"]); await sm.edit(content=text)
        else:
            sm=await channel.send(content=text); db.execute("INSERT INTO starboard_posts(guild_id,message_id,starboard_message_id) VALUES(?,?,?)",(guild.id,msg.id,sm.id))
    except Exception: pass

@bot.hybrid_command(name="sticky",description="Create or replace a sticky message in this channel")
@command_check("manage_messages")
@app_command_check("manage_messages")
async def sticky(ctx,content:str):
    old=db.fetchone("SELECT message_id FROM sticky_messages WHERE channel_id=?",(ctx.channel.id,))
    if old:
        try:
            m=await ctx.channel.fetch_message(old["message_id"]); await m.delete()
        except Exception: pass
    m=await ctx.channel.send(f"📌 **Sticky:**\n{content}")
    db.execute("INSERT OR REPLACE INTO sticky_messages(channel_id,message_id,content) VALUES(?,?,?)",(ctx.channel.id,m.id,content)); await ctx.send("✅ Sticky message updated.",delete_after=5)

@bot.hybrid_command(name="unsticky",description="Remove the sticky message")
@command_check("manage_messages")
@app_command_check("manage_messages")
async def unsticky(ctx):
    old=db.fetchone("SELECT message_id FROM sticky_messages WHERE channel_id=?",(ctx.channel.id,))
    if old:
        try: m=await ctx.channel.fetch_message(old["message_id"]); await m.delete()
        except Exception: pass
    db.execute("DELETE FROM sticky_messages WHERE channel_id=?",(ctx.channel.id,)); await ctx.send("✅ Sticky removed.",delete_after=5)

@bot.hybrid_command(name="say",description="Make the bot send a message")
@command_check("manage_messages")
@app_command_check("manage_messages")
async def say(ctx,message:str):
    await ctx.send(message)

@bot.hybrid_command(name="announce",description="Send an announcement embed")
@command_check("manage_guild")
@app_command_check("manage_guild")
async def announce(ctx,title:str,message:str):
    e=discord.Embed(title=title,description=message,color=discord.Color.blurple(),timestamp=utcnow()); e.set_footer(text=ctx.guild.name); await ctx.send(embed=e)

@bot.hybrid_command(name="botstatus",description="Show bot runtime status")
async def botstatus(ctx):
    latency=round(bot.latency*1000); await ctx.send(embed=discord.Embed(title="🤖 Bot Status",description=f"Latency: **{latency}ms**\nGuilds: **{len(bot.guilds)}**\nCommands: **{len(bot.commands)}**\nUptime tracking: active\nDatabase: SQLite/WAL",color=discord.Color.green()))

@bot.hybrid_command(name="unafk",description="Remove your AFK status")
async def unafk(ctx):
    db.execute("DELETE FROM afk WHERE guild_id=? AND user_id=?",(ctx.guild.id,ctx.author.id)); await ctx.send("✅ AFK removed.")


# ============================================================
# SIMPLE HELP / ERROR HANDLING / START
# ============================================================
@bot.hybrid_command(name="help",description="Show main bot categories")
async def help_cmd(ctx):
    e=discord.Embed(title="🤖 All-Rounder Bot",description="Moderation • AutoMod • Logging • Tickets • Giveaways • Invites • Stats • Welcome • Suggestions • Polls • AFK • Verification • Reaction Roles • Raid Protection • Custom Commands • Server tools",color=discord.Color.blurple()); e.add_field(name="🛡️ Moderation",value="`ban` `kick` `softban` `unban` `warn` `warnings` `clearwarnings` `history` `mute` `unmute` `nick` ",inline=False); e.add_field(name="📊 Stats",value="`mystats` `stats` `leaderboard` `invites` `inviteleaderboard`",inline=False); e.add_field(name="🎫 Tickets",value="`/setup_ticket` `/deploy_ticket` + persistent panels",inline=False); e.add_field(name="🎉 Giveaways",value="`giveaway` `/giveaway_end` `/giveaway_reroll` + restart recovery",inline=False); e.add_field(name="⚙️ Server",value="`/serverconfig` `/setlog` `/setmodlog` `/setwelcome` `/setautorole` `/setautomod` `lockdown` `unlockdown`",inline=False); await ctx.send(embed=e)

@bot.event
async def on_command_error(ctx,error):
    if isinstance(error,commands.CommandNotFound): return
    if isinstance(error,(commands.CheckFailure,app_commands.CheckFailure)): return await ctx.send("❌ You don't have permission to use this command.",ephemeral=True)
    if isinstance(error,commands.MissingRequiredArgument): return await ctx.send(f"❌ Missing argument: `{error.param.name}`",ephemeral=True)
    if isinstance(error,commands.BadArgument): return await ctx.send("❌ Invalid argument. Please check the command values.",ephemeral=True)
    log.exception("Command error: %s",error)

keep_alive()
TOKEN=os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable not found!")
app_command_count = len(bot.tree.get_commands())
if app_command_count > 100:
    raise RuntimeError(f"Application command count is {app_command_count}; Discord allows 100 global slash commands.")
log.info("Slash command budget: %d/100 global commands registered", app_command_count)

bot.run(TOKEN)

