import os
import random
import asyncio
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
OWNER_ID = 1255544682759323680  # Safely initialized to prevent NameError
ban_limit = 5
channel_limit = 3
spam_limit = 5
custom_prefix = "!"
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
intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.invites = True
intents.message_content = True
bot = commands.Bot(
    command_prefix=commands.when_mentioned_or(custom_prefix),
    intents=intents,
    help_command=None
)
def is_whitelisted(user: discord.Member, guild: discord.Guild) -> bool:
    return bool(OWNER_ID and user.id == OWNER_ID) or user.guild_permissions.administrator
def parse_time(duration_str: str) -> int:
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if not duration_str: return 60
    unit, val = duration_str[-1].lower(), duration_str[:-1]
    return int(val) * multipliers[unit] if unit in multipliers and val.isdigit() else 60
def _has_perm(member: discord.Member, permission: str) -> bool:
    return bool(getattr(member.guild_permissions, permission, False))
def _hierarchy_error(ctx, target: discord.Member) -> str | None:
    guild, author, bot_member = ctx.guild, ctx.author, ctx.guild.me
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
        return user.id == OWNER_ID or (isinstance(user, discord.Member) and _has_perm(user, permission))
    return app_commands.check(check)
def owner_or_permission(ctx, permission: str) -> bool:
    return ctx.author.id == OWNER_ID or _has_perm(ctx.author, permission)
@bot.event
async def on_ready():
    print(f"==================================================")
    print(f" Logged in successfully as {bot.user}")
    print(f" Bot ID: {bot.user.id}")
    print(f"==================================================")
    try:
        synced = await bot.tree.sync()
        print(f" [Slash Commands] Successfully synced {len(synced)} command(s).")
    except Exception as e:
        print(f" [Slash Commands] Failed to sync commands: {e}")
    for guild in bot.guilds:
        try:
            invites_cache[guild.id] = await guild.invites()
            print(f" [Invite Cache] Cached invites for guild: {guild.name}")
        except Exception as e:
            print(f" [Invite Cache] Could not cache invites for {guild.name}: {e}")
    print(f"==================================================")
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
        spam_tracking[user_id] = [t for t in spam_tracking[user_id] if current_time - t < 5]
        spam_tracking[user_id].append(current_time)
        if len(spam_tracking[user_id]) >= spam_limit:
            try:
                duration = timedelta(minutes=5)
                await message.author.timeout(duration, reason="Anti-Spam: Sending messages too quickly.")
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
@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    try:
        async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
            executor = entry.user
            if executor.bot or is_whitelisted(executor, guild):
                return
            current_time = datetime.now().timestamp()
            if executor.id not in nuke_tracking:
                nuke_tracking[executor.id] = {"bans": [], "channels": []}
            nuke_tracking[executor.id]["bans"] = [t for t in nuke_tracking[executor.id]["bans"] if current_time - t < 120]
            nuke_tracking[executor.id]["bans"].append(current_time)
            if len(nuke_tracking[executor.id]["bans"]) >= ban_limit:
                await guild.ban(executor, reason="Anti-Nuke Triggered: Mass banning members.")
                alert_channel = guild.system_channel
                if alert_channel:
                    await alert_channel.send(
                        f"🚨 **ANTI-NUKE ACTIVATED** 🚨\nUser {executor.mention} was automatically banned for mass banning members."
                    )
            break
    except Exception as e:
        print(f"Error in on_member_ban anti-nuke: {e}")
@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    guild = channel.guild
    try:
        async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
            executor = entry.user
            if executor.bot or is_whitelisted(executor, guild):
                return
            current_time = datetime.now().timestamp()
            if executor.id not in nuke_tracking:
                nuke_tracking[executor.id] = {"bans": [], "channels": []}
            nuke_tracking[executor.id]["channels"] = [t for t in nuke_tracking[executor.id]["channels"] if current_time - t < 120]
            nuke_tracking[executor.id]["channels"].append(current_time)
            if len(nuke_tracking[executor.id]["channels"]) >= channel_limit:
                await guild.ban(executor, reason="Anti-Nuke Triggered: Mass deleting channels.")
                alert_channel = guild.system_channel
                if alert_channel:
                    await alert_channel.send(
                        f"🚨 **ANTI-NUKE ACTIVATED** 🚨\nUser {executor.mention} was automatically banned for mass deleting channels."
                    )
            break
    except Exception as e:
        print(f"Error in on_guild_channel_delete anti-nuke: {e}")
class DynamicTicketModal(discord.ui.Modal):
    def __init__(self, button_id, button_label, questions):
        super().__init__(title=f"{button_label[:35]} Ticket")
        self.button_id, self.button_label, self.question_inputs = button_id, button_label, []
        for q in questions[:5]:
            inp = discord.ui.TextInput(label=q[:45], placeholder="Type your answer here...", style=discord.TextStyle.paragraph, required=False, max_length=500)
            self.question_inputs.append((q, inp)); self.add_item(inp)
    async def on_submit(self, interaction):
        guild, user = interaction.guild, interaction.user
        if not guild:
            return await interaction.response.send_message("❌ This can only be used inside a server.", ephemeral=True)
        config = ticket_configs.get(guild.id)
        if not config:
            return await interaction.response.send_message("❌ Ticket system is not configured.", ephemeral=True)
        category = guild.get_channel(config.get("category_id")) if config.get("category_id") else None
        if config.get("category_id") and category is None:
            return await interaction.response.send_message("❌ Ticket category no longer exists. Run `/setup_ticket` again.", ephemeral=True)
        role = guild.get_role(config.get("role_id")) if config.get("role_id") else None
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
        except discord.Forbidden:
            return await interaction.response.send_message("❌ Bot needs **Manage Channels** permission.", ephemeral=True)
        except Exception as e:
            print(f"[TICKET CREATE ERROR] {e}")
            return await interaction.response.send_message("❌ Ticket create karte waqt error aa gaya.", ephemeral=True)
        embed = build_ticket_embed(config, self.button_label, user, self.question_inputs)
        try:
            await channel.send(content=format_ticket_text(config.get("ticket_open_message", ""), user, role), embed=embed, view=TicketControlView())
        except Exception as e:
            print(f"[TICKET MESSAGE ERROR] {e}")
            try: await channel.delete(reason="Failed to send ticket message")
            except Exception: pass
            return await interaction.response.send_message("❌ Ticket create hua tha lekin message send nahi ho saka.", ephemeral=True)
        await interaction.response.send_message(f"✅ Your ticket has been created successfully: {channel.mention}", ephemeral=True)
def format_ticket_text(text, user, role=None):
    if not text: return None
    return text.replace("{user}", user.mention).replace("{role}", role.mention if role else "@here").replace("{username}", user.display_name)
def build_ticket_embed(config, button_label, user, answers=None):
    embed = discord.Embed(
        title=config.get("ticket_title", f"🎫 {button_label} Ticket").replace("{type}", button_label),
        description=config.get("ticket_desc", "Ticket opened by {user}\nPlease wait patiently.").replace("{user}", user.mention).replace("{type}", button_label),
        color=int(config.get("ticket_color", 3447003)),
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="Opened By", value=user.mention, inline=False)
    embed.add_field(name="Ticket Type", value=button_label, inline=False)
    for q, inp in answers or []:
        embed.add_field(name=q[:256], value=(inp.value.strip() if inp.value else "Not Provided")[:1024], inline=False)
    footer = config.get("ticket_footer", "")
    if footer: embed.set_footer(text=footer.replace("{user}", user.display_name).replace("{type}", button_label))
    return embed
class DynamicTicketButtonView(discord.ui.View):
    def __init__(self, buttons_data):
        super().__init__(timeout=None)
        for cid, data in buttons_data.items():
            self.add_item(DynamicTicketButton(cid, data["label"], data.get("questions", [])))
class DynamicTicketButton(discord.ui.Button):
    def __init__(self, custom_id, label, questions):
        super().__init__(style=discord.ButtonStyle.primary, label=label[:80], custom_id=custom_id)
        self.questions = questions
    async def callback(self, interaction):
        try:
            if self.questions:
                await interaction.response.send_modal(DynamicTicketModal(self.custom_id, self.label, self.questions))
            else:
                await interaction.response.defer(ephemeral=True)
                await create_ticket_without_questions(interaction, self.custom_id, self.label)
        except Exception as e:
            print(f"[BUTTON CALLBACK ERROR] {e}")
            try:
                if interaction.response.is_done(): await interaction.followup.send("❌ Ticket open karte waqt error aa gaya.", ephemeral=True)
                else: await interaction.response.send_message("❌ Ticket open karte waqt error aa gaya.", ephemeral=True)
            except Exception: pass
async def create_ticket_without_questions(interaction, button_id, button_label):
    guild, user = interaction.guild, interaction.user
    config = ticket_configs.get(guild.id) if guild else None
    if not guild: return await interaction.followup.send("❌ This can only be used inside a server.", ephemeral=True)
    if not config: return await interaction.followup.send("❌ Ticket system is not configured.", ephemeral=True)
    category = guild.get_channel(config.get("category_id")) if config.get("category_id") else None
    role = guild.get_role(config.get("role_id")) if config.get("role_id") else None
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True)
    }
    if guild.me: overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True, manage_messages=True, attach_files=True, embed_links=True)
    if role: overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True)
    clean = "".join(c for c in user.name.lower() if c.isalnum() or c in "-_") or "user"
    try:
        channel = await guild.create_text_channel(name=f"ticket-{clean}-{user.id}"[:95], category=category, overwrites=overwrites, reason=f"Support ticket opened by {user}")
    except discord.Forbidden:
        return await interaction.followup.send("❌ Bot needs **Manage Channels** permission.", ephemeral=True)
    except Exception as e:
        print(f"[DIRECT TICKET ERROR] {e}")
        return await interaction.followup.send("❌ Ticket create karte waqt error aa gaya.", ephemeral=True)
    await channel.send(content=format_ticket_text(config.get("ticket_open_message", ""), user, role), embed=build_ticket_embed(config, button_label, user), view=TicketControlView())
    await interaction.followup.send(f"✅ Ticket created successfully: {channel.mention}", ephemeral=True)
class TicketControlView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="close_ticket_btn")
    async def close_ticket(self, interaction, button):
        await interaction.response.send_message("🔒 Ticket closing in 5 seconds...")
        if ticket_log_channel_id:
            log_chan = interaction.guild.get_channel(ticket_log_channel_id)
            if log_chan:
                try:
                    messages = [m async for m in interaction.channel.history(limit=100, oldest_first=True)]
                    content = f"Transcript for #{interaction.channel.name}\nClosed by: {interaction.user}\nClosed at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
                    for m in messages:
                        content += f"[{m.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {m.author}: {m.content or '[Embed/Attachment/Component]'}\n"
                    path = f"transcript_{interaction.channel.id}.txt"
                    with open(path, "w", encoding="utf-8") as f: f.write(content)
                    await log_chan.send(f"📁 **Transcript for closed ticket:** `#{interaction.channel.name}`", file=discord.File(path))
                    try: os.remove(path)
                    except Exception: pass
                except Exception as e: print(f"[TRANSCRIPT ERROR] {e}")
        await asyncio.sleep(5)
        try: await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")
        except discord.NotFound: pass
        except discord.Forbidden: print("[CLOSE ERROR] Bot doesn't have permission to delete the channel.")
        except Exception as e: print(f"[CLOSE ERROR] {e}")
class AddMoreButtonModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Add New Ticket Button")
        self.btn_name = discord.ui.TextInput(label="Button Name", placeholder="e.g. Bug Report", required=True, max_length=50)
        self.q1 = discord.ui.TextInput(label="Question 1", placeholder="Optional question...", required=False, max_length=100)
        self.q2 = discord.ui.TextInput(label="Question 2", placeholder="Optional question...", required=False, max_length=100)
        self.q3 = discord.ui.TextInput(label="Question 3", placeholder="Optional question...", required=False, max_length=100)
        self.q4 = discord.ui.TextInput(label="Question 4", placeholder="Optional question...", required=False, max_length=100)
        for x in (self.btn_name, self.q1, self.q2, self.q3, self.q4): self.add_item(x)
    async def on_submit(self, interaction):
        guild = interaction.guild
        if not guild or guild.id not in ticket_configs: return await interaction.response.send_message("❌ No active ticket config found! Run `/setup_ticket` first.", ephemeral=True)
        questions = [q.value.strip() for q in (self.q1, self.q2, self.q3, self.q4) if q.value and q.value.strip()]
        cid = f"custom_ticket_{random.randint(100000, 999999)}"
        ticket_configs[guild.id]["buttons"][cid] = {"label": self.btn_name.value, "questions": questions}
        await interaction.response.send_message(f"✅ Button **{self.btn_name.value}** added!", view=PostButtonManagerView(), ephemeral=True)
class EditButtonModal(discord.ui.Modal):
    def __init__(self, guild_id, cid):
        data = ticket_configs[guild_id]["buttons"][cid]
        super().__init__(title="Edit Ticket Button")
        self.guild_id, self.cid = guild_id, cid
        self.btn_name = discord.ui.TextInput(label="Button Name", default=data["label"][:50], max_length=50)
        qs = data.get("questions", []) + [""] * 4
        self.qs = [discord.ui.TextInput(label=f"Question {i+1}", default=qs[i][:100], required=False, max_length=100) for i in range(4)]
        self.add_item(self.btn_name)
        for q in self.qs: self.add_item(q)
    async def on_submit(self, interaction):
        ticket_configs[self.guild_id]["buttons"][self.cid] = {"label": self.btn_name.value, "questions": [q.value.strip() for q in self.qs if q.value and q.value.strip()]}
        await interaction.response.send_message(f"✅ Button **{self.btn_name.value}** updated!", view=PostButtonManagerView(), ephemeral=True)
class ButtonEditSelect(discord.ui.Select):
    def __init__(self, guild_id):
        self.guild_id = guild_id
        buttons = ticket_configs.get(guild_id, {}).get("buttons", {})
        options = [discord.SelectOption(label=d["label"][:100], value=cid) for cid, d in buttons.items()]
        super().__init__(placeholder="Select a button to edit...", min_values=1, max_values=1, options=options[:25])
    async def callback(self, interaction):
        await interaction.response.send_modal(EditButtonModal(self.guild_id, self.values[0]))
class RemoveButtonSelect(discord.ui.Select):
    def __init__(self, guild_id):
        self.guild_id = guild_id
        buttons = ticket_configs.get(guild_id, {}).get("buttons", {})
        options = [discord.SelectOption(label=d["label"][:100], value=cid) for cid, d in buttons.items()]
        super().__init__(placeholder="Select a button to remove...", min_values=1, max_values=1, options=options[:25])
    async def callback(self, interaction):
        cid = self.values[0]; buttons = ticket_configs[interaction.guild.id]["buttons"]
        if cid not in buttons: return await interaction.response.send_message("❌ Button already removed.", ephemeral=True)
        label = buttons[cid]["label"]; del buttons[cid]
        await interaction.response.send_message(f"🗑️ Button **{label}** removed!", view=PostButtonManagerView(), ephemeral=True)
class EditButtonView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=60)
        if ticket_configs.get(guild_id, {}).get("buttons"): self.add_item(ButtonEditSelect(guild_id))
class RemoveButtonView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=60)
        if ticket_configs.get(guild_id, {}).get("buttons"): self.add_item(RemoveButtonSelect(guild_id))
class TicketMessageModal(discord.ui.Modal):
    def __init__(self, guild_id):
        config = ticket_configs[guild_id]
        super().__init__(title="Edit Ticket Message")
        self.guild_id = guild_id
        self.title_input = discord.ui.TextInput(label="Ticket Embed Title", default=config.get("ticket_title", "")[:256], max_length=256)
        self.desc_input = discord.ui.TextInput(label="Ticket Embed Description", default=config.get("ticket_desc", "")[:4000], style=discord.TextStyle.paragraph, max_length=4000)
        self.open_input = discord.ui.TextInput(label="Opening Message", default=config.get("ticket_open_message", "")[:1000], required=False, max_length=1000)
        self.footer_input = discord.ui.TextInput(label="Embed Footer", default=config.get("ticket_footer", "")[:2048], required=False, max_length=2048)
        self.color_input = discord.ui.TextInput(label="Embed Color (hex)", default=f"{config.get('ticket_color',3447003):06X}", required=False, max_length=6)
        for x in (self.title_input, self.desc_input, self.open_input, self.footer_input, self.color_input): self.add_item(x)
    async def on_submit(self, interaction):
        config = ticket_configs[self.guild_id]
        config["ticket_title"], config["ticket_desc"] = self.title_input.value, self.desc_input.value
        config["ticket_open_message"], config["ticket_footer"] = self.open_input.value, self.footer_input.value
        try: config["ticket_color"] = int(self.color_input.value.replace("#",""), 16)
        except ValueError: config["ticket_color"] = 3447003
        await interaction.response.send_message("✅ Ticket message/embed updated!", view=PostButtonManagerView(), ephemeral=True)
class PostButtonManagerView(discord.ui.View):
    def __init__(self): super().__init__(timeout=180)
    @discord.ui.button(label="Add Button", style=discord.ButtonStyle.secondary, emoji="➕")
    async def add_btn(self, interaction, button): await interaction.response.send_modal(AddMoreButtonModal())
    @discord.ui.button(label="Edit Button", style=discord.ButtonStyle.primary, emoji="✏️")
    async def edit_btn(self, interaction, button):
        if not ticket_configs.get(interaction.guild.id, {}).get("buttons"): return await interaction.response.send_message("❌ No buttons available.", ephemeral=True)
        await interaction.response.send_message("Select the button to edit:", view=EditButtonView(interaction.guild.id), ephemeral=True)
    @discord.ui.button(label="Remove Button", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def remove_btn(self, interaction, button):
        if not ticket_configs.get(interaction.guild.id, {}).get("buttons"): return await interaction.response.send_message("❌ No buttons available.", ephemeral=True)
        await interaction.response.send_message("Select the button to remove:", view=RemoveButtonView(interaction.guild.id), ephemeral=True)
    @discord.ui.button(label="Edit Ticket Message", style=discord.ButtonStyle.secondary, emoji="📝")
    async def edit_message(self, interaction, button): await interaction.response.send_modal(TicketMessageModal(interaction.guild.id))
    @discord.ui.button(label="Deploy Panel", style=discord.ButtonStyle.success, emoji="🚀")
    async def deploy_panel(self, interaction, button):
        config = ticket_configs.get(interaction.guild.id)
        if not config or not config.get("buttons"): return await interaction.response.send_message("❌ You must have at least one button.", ephemeral=True)
        embed = discord.Embed(title=config["title"], description=config["desc"], color=discord.Color.blue())
        try:
            await interaction.channel.send(embed=embed, view=DynamicTicketButtonView(config["buttons"]))
            await interaction.response.send_message("🎉 **Ticket Panel deployed successfully!**", ephemeral=True)
        except discord.Forbidden: await interaction.response.send_message("❌ Bot cannot send messages in this channel.", ephemeral=True)
        except Exception as e:
            print(f"[DEPLOY ERROR] {e}")
            await interaction.response.send_message("❌ Panel deploy karte waqt error aa gaya.", ephemeral=True)
class TicketSetupModal(discord.ui.Modal):
    def __init__(self, category, role, existing=None):
        super().__init__(title="Configure Ticket Panel")
        self.category, self.role, self.existing = category, role, existing
        self.panel_title = discord.ui.TextInput(label="Panel Title", default=(existing or {}).get("title","Support Hub")[:100], max_length=100)
        self.panel_desc = discord.ui.TextInput(label="Panel Description", style=discord.TextStyle.paragraph, default=(existing or {}).get("desc","Click a button below to open a support ticket.")[:4000], max_length=4000)
        self.btn_name = discord.ui.TextInput(label="First Button Name", default=((existing or {}).get("buttons") or {}).get(next(iter((existing or {}).get("buttons",{})), ""), {}).get("label","Support")[:50], max_length=50)
        self.q1 = discord.ui.TextInput(label="Question 1", required=False, max_length=100)
        self.q2 = discord.ui.TextInput(label="Question 2", required=False, max_length=100)
        for x in (self.panel_title,self.panel_desc,self.btn_name,self.q1,self.q2): self.add_item(x)
    async def on_submit(self, interaction):
        guild = interaction.guild
        if not guild: return await interaction.response.send_message("❌ Server not found.", ephemeral=True)
        old = ticket_configs.get(guild.id)
        if old:
            old.update({"category_id":self.category.id,"role_id":self.role.id,"title":self.panel_title.value,"desc":self.panel_desc.value})
            await interaction.response.send_message("✅ Existing ticket setup updated. Buttons were preserved.", view=PostButtonManagerView(), ephemeral=True)
            return
        cid = f"custom_ticket_{random.randint(100000,999999)}"
        qs = [q.value.strip() for q in (self.q1,self.q2) if q.value and q.value.strip()]
        ticket_configs[guild.id] = {
            "category_id":self.category.id,"role_id":self.role.id,"title":self.panel_title.value,"desc":self.panel_desc.value,
            "ticket_title":f"🎫 {self.btn_name.value} Ticket","ticket_desc":"Ticket opened by {user.mention}\nPlease wait patiently.",
            "ticket_open_message":"{role} New ticket opened by {user}","ticket_footer":"User ID: {user}","ticket_color":3447003,
            "buttons":{cid:{"label":self.btn_name.value,"questions":qs}}
        }
        await interaction.response.send_message("✅ **Ticket setup saved!** Use the manager below to add/edit buttons, questions and ticket message.", view=PostButtonManagerView(), ephemeral=True)
@bot.tree.command(
    name="setup_ticket",
    description="Setup the ticket system"
)
@app_commands.describe(
    category="The category where tickets will be created",
    role="The support role that can view tickets"
)
@permission_check("administrator")
@app_permission_check("administrator")
async def setup_ticket(
    interaction: discord.Interaction,
    category: discord.CategoryChannel,
    role: discord.Role
):
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Ye command sirf server ke andar use ho sakti hai.",
            ephemeral=True
        )
        return
    bot_member = interaction.guild.me
    if bot_member:
        required_permissions = [
            ("Manage Channels", bot_member.guild_permissions.manage_channels),
            ("Send Messages", bot_member.guild_permissions.send_messages),
            ("View Channels", bot_member.guild_permissions.view_channel)
        ]
        missing = [
            name for name, has_permission in required_permissions
            if not has_permission
        ]
        if missing:
            await interaction.response.send_message(
                "❌ Bot ke paas required permissions nahi hain:\n\n" +
                "\n".join(f"• {permission}" for permission in missing),
                ephemeral=True
            )
            return
    try:
        await interaction.response.send_modal(
            TicketSetupModal(category, role)
        )
    except discord.InteractionResponded:
        print("[SETUP ERROR] Interaction already responded.")
    except Exception as e:
        print(f"[SETUP MODAL ERROR] {e}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Setup modal open karte waqt error aa gaya.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ Setup modal open karte waqt error aa gaya.",
                    ephemeral=True
                )
        except Exception as followup_error:
            print(f"[SETUP FOLLOWUP ERROR] {followup_error}")
@setup_ticket.error
async def setup_ticket_error(interaction, error):
    message = "❌ You need **Administrator** permission to use this command."
    if not isinstance(error, (app_commands.CheckFailure, commands.CheckFailure)):
        print(f"[SETUP COMMAND ERROR] {error}")
        message = "❌ `/setup_ticket` execute karte waqt error aa gaya."
    try:
        if interaction.response.is_done(): await interaction.followup.send(message, ephemeral=True)
        else: await interaction.response.send_message(message, ephemeral=True)
    except Exception as e: print(f"[ERROR HANDLER ERROR] {e}")
@bot.hybrid_command(name="giveaway", description="Start an interactive giveaway with optional fixed winner.")
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
        description=f"**Prize:** {prize}\n**Winner(s):** `{winners_count}`\n**Hosted by:** {author.mention}\n\nReact with 🎉 to enter!",
        color=discord.Color.gold(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text=f"Ends in {duration}")
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("🎉")
    await asyncio.sleep(seconds)
    try:
        msg = await channel.fetch_message(msg.id)
    except Exception:
        return
    reaction = discord.utils.get(msg.reactions, emoji="🎉")
    participants = []
    if reaction:
        async for u in reaction.users():
            if not u.bot:
                participants.append(u.id)
    chosen_winners = []
    if fixed_winner and fixed_winner.id in participants:
        chosen_winners.append(fixed_winner)
        participants.remove(fixed_winner.id)
    while len(chosen_winners) < winners_count and participants:
        winner_id = random.choice(participants)
        participants.remove(winner_id)
        member = guild.get_member(winner_id)
        if member:
            chosen_winners.append(member)
    if chosen_winners:
        winners_mention = ", ".join([w.mention for w in chosen_winners])
        ended_embed = discord.Embed(
            title="🎉 GIVEAWAY ENDED 🎉",
            description=f"**Prize:** {prize}\n**Winner(s):** {winners_mention} 🏆\n**Hosted by:** {author.mention}",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        await msg.edit(embed=ended_embed, view=None)
        await channel.send(f"🎊 Congratulations {winners_mention}! You won **{prize}**!")
    else:
        ended_embed = discord.Embed(
            title="🎉 GIVEAWAY ENDED 🎉",
            description=f"**Prize:** {prize}\n❌ No valid participants found.",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )
        await msg.edit(embed=ended_embed, view=None)
@bot.hybrid_command(name="set_ban_limit", description="Set anti-nuke max ban threshold limit.")
@permission_check("administrator")
@app_permission_check("administrator")
async def set_ban_limit(ctx: commands.Context, limit: int):
    global ban_limit
    ban_limit = limit
    res = f"✅ **Anti-Nuke Ban Limit updated to:** `{ban_limit}` bans / 2 mins"
    await ctx.send(res)
@bot.hybrid_command(name="set_channel_limit", description="Set anti-nuke max channel delete threshold limit.")
@permission_check("administrator")
@app_permission_check("administrator")
async def set_channel_limit(ctx: commands.Context, limit: int):
    global channel_limit
    channel_limit = limit
    await ctx.send(f"✅ **Anti-Nuke Channel Delete Limit updated to:** `{channel_limit}` channels / 2 mins")
@bot.hybrid_command(name="set_spam_limit", description="Set max allowed messages within 5 seconds before mute.")
@permission_check("administrator")
@app_permission_check("administrator")
async def set_spam_limit(ctx: commands.Context, messages_count: int):
    global spam_limit
    spam_limit = messages_count
    await ctx.send(f"✅ **Anti-Spam Limit updated to:** `{spam_limit}` msgs / 5 sec")
@bot.hybrid_command(name="set_prefix", description="Set custom prefix for text commands.")
@permission_check("administrator")
@app_permission_check("administrator")
async def set_prefix(ctx: commands.Context, prefix: str):
    global custom_prefix
    custom_prefix = prefix
    bot.command_prefix = commands.when_mentioned_or(custom_prefix)
    await ctx.send(f"✅ **Custom Prefix updated to:** `{custom_prefix}`")
@bot.hybrid_command(name="ticket_log_channel", description="Set log channel for closed ticket transcripts.")
@permission_check("administrator")
@app_permission_check("administrator")
async def ticket_log_channel(ctx: commands.Context, channel: discord.TextChannel):
    global ticket_log_channel_id
    ticket_log_channel_id = channel.id
    await ctx.send(f"✅ **Ticket Transcript Log Channel set to:** {channel.mention}")
@bot.hybrid_command(name="set_ticket_ping", description="Customize ticket opening ping message.")
@permission_check("administrator")
@app_permission_check("administrator")
async def set_ticket_ping(ctx: commands.Context, message: str):
    global custom_ticket_ping
    custom_ticket_ping = message
    await ctx.send(f"✅ **Ticket Open Ping updated!**\nFormat: `{message}`")
class SimpleWelcomeModal(Modal, title="Configure Welcome Message"):
    wel_msg = TextInput(
        label="Welcome Message",
        style=discord.TextStyle.paragraph,
        default="Hey {user}, welcome to **{server}**! Member count: {count}",
        max_length=1000
    )
    wel_img = TextInput(
        label="Banner Image URL (Optional)",
        required=False,
        placeholder="Paste image link here or leave blank",
        default=""
    )
    async def on_submit(self, interaction: discord.Interaction):
        global custom_welcome_msg, custom_welcome_img, welcome_enabled
        custom_welcome_msg = self.wel_msg.value
        custom_welcome_img = self.wel_img.value.strip() if self.wel_img.value else None
        welcome_enabled = True
        await interaction.response.send_message(
            f"✅ **Welcome system fully updated!**\n\n💬 **Message:** `{custom_welcome_msg}`",
            ephemeral=True
        )
class WelcomeSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
    @discord.ui.select(cls=discord.ui.ChannelSelect, placeholder="Select Welcome Channel...", channel_types=[discord.ChannelType.text], min_values=1, max_values=1)
    async def select_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        selected_channel = select.values[0]
        global welcome_channel_id
        welcome_channel_id = selected_channel.id
        await interaction.response.send_modal(SimpleWelcomeModal())
@bot.hybrid_command(name="setup_welcome", description="Configure custom welcome channel, message and banner.")
@permission_check("administrator")
@app_permission_check("administrator")
async def setup_welcome(ctx: commands.Context):
    view = WelcomeSelectView()
    msg = "📌 **Please select your welcome channel from the dropdown below:**"
    await ctx.send(msg, view=view, ephemeral=True)
@bot.hybrid_command(name="disable_welcome", description="Turn off welcome system.")
@permission_check("administrator")
@app_permission_check("administrator")
async def disable_welcome(ctx: commands.Context):
    global welcome_enabled
    welcome_enabled = False
    await ctx.send("❌ **Welcome system has been disabled.**")
@bot.hybrid_command(name="setup_invitelog", description="Set channel for invite logs.")
@permission_check("administrator")
@app_permission_check("administrator")
async def setup_invitelog(ctx: commands.Context, channel: discord.TextChannel = None):
    global invite_log_channel_id
    target = channel or ctx.channel
    invite_log_channel_id = target.id
    await ctx.send(f"✅ **Invite Logger set to:** {target.mention}")
@bot.hybrid_command(name="invites", description="Check invite stats of a server member.")
async def invites(ctx: commands.Context, member: discord.Member = None):
    target = member or ctx.author
    guild = ctx.guild
    total_uses = 0
    try:
        guild_invites = await guild.invites()
        for inv in guild_invites:
            if inv.inviter and inv.inviter.id == target.id:
                total_uses += inv.uses
    except Exception:
        pass
    embed = discord.Embed(
        title=f"Invite Stats: {target.display_name}",
        description=f"👤 **Member:** {target.mention}\n📈 **Total Invites:** `{total_uses}`",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)
@bot.hybrid_command(name="ban", description="Permanently ban a member.")
@app_commands.describe(member="The member to ban", reason="Reason for the ban")
@permission_check("ban_members")
@app_permission_check("ban_members")
async def ban_cmd(ctx: commands.Context, member: discord.Member, reason: str = "No reason provided"):
    guild = ctx.guild
    hierarchy = _hierarchy_error(ctx, member)
    if hierarchy:
        await ctx.send(hierarchy)
        return
    if is_whitelisted(member, guild) and member.id != ctx.author.id:
        await ctx.send("❌ **Access Denied:** User is whitelisted.", ephemeral=True)
        return
    await member.ban(reason=reason)
    await ctx.send(f"🔨 **{member.mention} banned successfully!**")
@bot.hybrid_command(name="mute", description="Timeout a member for a specified duration.")
@app_commands.describe(member="The member to mute", duration="Duration (e.g. 10m, 1h)", reason="Reason for timeout")
@permission_check("moderate_members")
@app_permission_check("moderate_members")
async def mute_cmd(ctx: commands.Context, member: discord.Member, duration: str, reason: str = "No reason provided"):
    guild = ctx.guild
    hierarchy = _hierarchy_error(ctx, member)
    if hierarchy:
        await ctx.send(hierarchy)
        return
    if is_whitelisted(member, guild) and member.id != ctx.author.id:
        await ctx.send("❌ **Access Denied:** User is whitelisted.", ephemeral=True)
        return
    seconds = parse_time(duration)
    await member.timeout(timedelta(seconds=seconds), reason=reason)
    await ctx.send(f"🤐 **{member.mention} timed out for {duration}!**")
@bot.hybrid_command(name="purge", description="Bulk delete messages in current channel.")
@app_commands.describe(amount="Number of messages to delete (1-100)")
@permission_check("manage_messages")
@app_permission_check("manage_messages")
async def purge_cmd(ctx: commands.Context, amount: int):
    channel = ctx.channel
    if amount < 1 or amount > 100:
        await ctx.send("❌ Please specify an amount between 1 and 100.", ephemeral=True)
        return
    if ctx.interaction:
        await ctx.interaction.response.defer(ephemeral=True)
        deleted = await channel.purge(limit=amount)
        await ctx.interaction.followup.send(f"🧹 Successfully deleted **{len(deleted)}** messages!", ephemeral=True)
    else:
        deleted = await channel.purge(limit=amount)
        await ctx.send(f"🧹 Successfully deleted **{len(deleted)}** messages!", delete_after=5)
@bot.hybrid_command(name="role", description="Assign or remove a role from a member easily.")
@app_commands.describe(action="add or remove", member="Target member", role="Target role")
@permission_check("manage_roles")
@app_permission_check("manage_roles")
async def role_cmd(ctx: commands.Context, action: Literal["add", "remove"], member: discord.Member, role: discord.Role):
    user = ctx.author
    bot_member = ctx.guild.me
    if bot_member and role >= bot_member.top_role:
        return await ctx.send("❌ I cannot manage a role equal to or higher than my highest role.")
    if user.id != OWNER_ID and role >= user.top_role:
        return await ctx.send("❌ You cannot manage a role equal to or higher than your highest role.")
    if member.id != user.id and user.id != OWNER_ID and member.top_role >= user.top_role:
        return await ctx.send("❌ You cannot manage a member with an equal or higher role than yours.")
    if action == "add":
        await member.add_roles(role, reason=f"Managed by {user}")
        res = f"✅ Successfully added **{role.name}** to {member.mention}!"
    else:
        await member.remove_roles(role, reason=f"Managed by {user}")
        res = f"✅ Successfully removed **{role.name}** from {member.mention}!"
    await ctx.send(res)
@bot.hybrid_command(name="dmall", description="Send DM announcement to all server members.")
@app_commands.describe(message="The message you want to broadcast", as_embed="True for Embed format, False for Plain Text")
@permission_check("administrator")
@app_permission_check("administrator")
async def dmall(ctx: commands.Context, message: str, as_embed: bool = False):
    guild = ctx.guild
    format_type = "Embed" if as_embed else "Plain Text"
    await ctx.send(f"⏳ **Starting DM Broadcast ({format_type})...** Safe delay active.")
    success_count, failed_count = 0, 0
    for member in guild.members:
        if member.bot:
            continue
        try:
            if as_embed:
                embed = discord.Embed(title=f"Announcement from {guild.name}", description=message, color=discord.Color.gold())
                await member.send(embed=embed)
            else:
                await member.send(content=message)
            success_count += 1
            await asyncio.sleep(1.5)
        except Exception:
            failed_count += 1
    await ctx.send(f"✅ Sent ({format_type}): {success_count} | ❌ Failed: {failed_count}")
@bot.event
async def on_member_join(member):
    guild = member.guild
    inviter_user = None
    try:
        old_invites = invites_cache.get(guild.id, [])
        new_invites = await guild.invites()
        invites_cache[guild.id] = new_invites
        for old_inv in old_invites:
            new_inv = discord.utils.get(new_invites, code=old_inv.code)
            if new_inv and new_inv.uses > old_inv.uses:
                inviter_user = old_inv.inviter
                break
    except Exception:
        pass
    global invite_log_channel_id
    if invite_log_channel_id:
        log_channel = guild.get_channel(invite_log_channel_id)
        if log_channel:
            inviter_str = inviter_user.mention if inviter_user else "Unknown Link"
            embed = discord.Embed(
                title="📥 Member Joined via Invite",
                description=f"**Member:** {member.mention}\n**Invited By:** {inviter_str}",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await log_channel.send(embed=embed)
    global welcome_enabled, welcome_channel_id, custom_welcome_msg, custom_welcome_img
    if not welcome_enabled:
        return
    target_channel = guild.get_channel(welcome_channel_id) if welcome_channel_id else guild.system_channel
    if target_channel:
        if custom_welcome_msg:
            description_text = custom_welcome_msg.format(
                user=member.mention,
                server=guild.name,
                count=guild.member_count,
                inviter=inviter_user.name if inviter_user else "Unknown"
            )
        else:
            description_text = f"Hey {member.mention}, welcome to **{guild.name}**!"
        embed = discord.Embed(title="Welcome!", description=description_text, color=discord.Color.blue())
        embed.set_thumbnail(url=member.display_avatar.url)
        if custom_welcome_img:
            embed.set_image(url=custom_welcome_img)
        embed.set_footer(text=f"Member #{guild.member_count}")
        await target_channel.send(content=f"Welcome {member.mention}!", embed=embed)
@bot.hybrid_command(name="antinuke_help", description="Show all Anti-Nuke configuration commands.")
async def help_antinuke(ctx: commands.Context):
    embed = discord.Embed(title="🛡️ Anti-Nuke & Anti-Spam Commands", color=discord.Color.red())
    embed.add_field(name="/set_ban_limit <limit>", value="Set max ban limit threshold", inline=False)
    embed.add_field(name="/set_channel_limit <limit>", value="Set max channel deletion limit", inline=False)
    embed.add_field(name="/set_spam_limit <msgs>", value="Set message spam speed threshold", inline=False)
    await ctx.send(embed=embed)
@bot.hybrid_command(name="ticket_help", description="Show all Ticket Panel management commands.")
async def help_ticket(ctx: commands.Context):
    embed = discord.Embed(title="🎫 Ticket System Commands", color=discord.Color.blue())
    embed.add_field(name="/setup_ticket", value="Interactive wizard to set category, role, title & buttons", inline=False)
    embed.add_field(name="/add_ticket_button <name> <q1> [q2] [q3]", value="Add custom buttons and questions easily", inline=False)
    embed.add_field(name="/ticket_log_channel <channel>", value="Set channel for transcripts", inline=False)
    embed.add_field(name="/set_ticket_ping <msg>", value="Set custom mention message", inline=False)
    await ctx.send(embed=embed)
@bot.hybrid_command(name="giveaway_help", description="Show all Giveaway system commands.")
async def help_giveaway(ctx: commands.Context):
    embed = discord.Embed(title="🎉 Giveaway System Commands", color=discord.Color.gold())
    embed.add_field(name="/giveaway <prize> <duration> [winners] [fixed_winner]", value="Start giveaway with optional fixed winner", inline=False)
    await ctx.send(embed=embed)
@bot.hybrid_command(name="welcome_help", description="Show all Welcome System commands.")
async def help_welcome(ctx: commands.Context):
    embed = discord.Embed(title="👋 Welcome System Commands", color=discord.Color.green())
    embed.add_field(name="/setup_welcome", value="Set welcome channel, message, and banner via interactive modal", inline=False)
    embed.add_field(name="/disable_welcome", value="Turn off welcome system", inline=False)
    await ctx.send(embed=embed)
@bot.hybrid_command(name="invites_help", description="Show all Invite Tracker commands.")
async def help_invites(ctx: commands.Context):
    embed = discord.Embed(title="📊 Invite Tracker Commands", color=discord.Color.gold())
    embed.add_field(name="/setup_invitelog [channel]", value="Set invite logging channel", inline=False)
    embed.add_field(name="/invites [member]", value="Check member invite count", inline=False)
    await ctx.send(embed=embed)
@bot.hybrid_command(name="moderation_help", description="Show all Moderation commands.")
async def help_mod(ctx: commands.Context):
    embed = discord.Embed(title="🔨 Moderation Commands", color=discord.Color.purple())
    embed.add_field(name="/ban <member> [reason]", value="Permanently ban a member", inline=False)
    embed.add_field(name="/mute <member> <time> [reason]", value="Timeout member (e.g. 10m, 1h)", inline=False)
    embed.add_field(name="/purge <amount>", value="Clear up to 100 messages", inline=False)
    embed.add_field(name="/role <add/remove> <member> <role>", value="Manage roles quickly", inline=False)
    embed.add_field(name="/set_prefix <prefix>", value="Change text prefix", inline=False)
    embed.add_field(name="/dmall <message>", value="Broadcast DMs to server members", inline=False)
    await ctx.send(embed=embed)
keep_alive()
BOT_TOKEN = os.getenv("DISCORD_TOKEN")
if BOT_TOKEN:
    bot.run(BOT_TOKEN)
else:
    print("❌ Error: DISCORD_TOKEN environment variable not found!")


