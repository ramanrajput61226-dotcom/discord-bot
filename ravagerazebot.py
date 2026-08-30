import discord
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput
from discord import app_commands
import asyncio
import random
from datetime import datetime, timedelta, timezone
from keep_alive import keep_alive
import os

# ==================== INTENTS SETUP ====================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.moderation = True
intents.invites = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ==================== GLOBAL CONFIGURATION & TRACKERS ====================

ban_limit = 5
channel_limit = 4
spam_limit = 5
spam_time_window = 5

ban_tracker = {}
channel_tracker = {}
banned_members_history = []
invites_cache = {}
message_tracker = {}

ticket_support_role_id = None
welcome_channel_id = None
welcome_enabled = True
custom_welcome_msg = None
custom_welcome_img = None
invite_log_channel_id = None

# ==================== HELPER FUNCTIONS ====================

def clean_tracker(tracker, user_id, time_limit_seconds=120):
    now = datetime.now(timezone.utc)
    if user_id not in tracker:
        tracker[user_id] = []
    tracker[user_id] = [t for t in tracker[user_id] if (now - t).total_seconds() <= time_limit_seconds]
    return tracker[user_id]


def is_whitelisted(executor, guild):
    if executor.id == guild.owner_id or executor.id == bot.user.id:
        return True
    bot_member = guild.get_member(bot.user.id)
    if bot_member and executor.top_role >= bot_member.top_role:
        return True
    return False


def parse_time(time_str):
    unit = time_str[-1].lower()
    val = int(time_str[:-1])
    if unit == 's': return val
    if unit == 'm': return val * 60
    if unit == 'h': return val * 3600
    if unit == 'd': return val * 86400
    return int(time_str)


# ==================== BOT READY & INSTANT SLASH SYNC ====================

@bot.event
async def on_ready():
    print(f"✅ Bot Online & Ready: {bot.user}")
    
    # 1. Register persistent UI views for tickets
    bot.add_view(TicketButtonView())
    bot.add_view(TicketControlView())

    # 2. Global Sync Across All Servers (Safe & Fast)
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} Slash Commands globally!")
    except Exception as e:
        print(f"❌ Global Slash Sync Error: {e}")

# ==================== ANTI-SPAM & ANTI-NUKE LOGIC ====================

async def punish_nuker(guild, executor, reason):
    try:
        roles_to_remove = [role for role in executor.roles if role.name != "@everyone" and role < guild.me.top_role]
        if roles_to_remove:
            await executor.remove_roles(*roles_to_remove, reason=f"[ANTI-NUKE] {reason}")

        await executor.timeout(timedelta(days=1), reason=f"[ANTI-NUKE] {reason}")

        if guild.system_channel:
            embed = discord.Embed(
                title="🚨 ANTI-NUKE SYSTEM TRIGGERED!",
                description=f"**Attacker:** {executor.mention} (`{executor.id}`)\n**Reason:** {reason}\n**Action:** Roles Stripped & 24h Timeout Applied 🚫",
                color=discord.Color.red()
            )
            await guild.system_channel.send(embed=embed)
    except Exception as e:
        print(f"[ANTI-NUKE ERROR] {e}")


@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    if not is_whitelisted(message.author, message.guild):
        user_id = message.author.id
        now = datetime.now(timezone.utc)
        
        if user_id not in message_tracker:
            message_tracker[user_id] = []
        
        message_tracker[user_id] = [t for t in message_tracker[user_id] if (now - t).total_seconds() <= spam_time_window]
        message_tracker[user_id].append(now)

        if len(message_tracker[user_id]) >= spam_limit:
            message_tracker[user_id] = []
            try:
                await message.author.timeout(timedelta(minutes=10), reason="[ANTI-SPAM] Exceeded message speed limit")
                await message.channel.send(f"🤐 {message.author.mention} has been muted for 10 minutes for spamming!", delete_after=10)
            except Exception:
                pass

    await bot.process_commands(message)


@bot.event
async def on_member_ban(guild, user):
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
        executor = entry.user
        if is_whitelisted(executor, guild):
            return

        now = datetime.now(timezone.utc)
        user_bans = clean_tracker(ban_tracker, executor.id, 120)
        user_bans.append(now)
        banned_members_history.append((user.id, now))

        if len(user_bans) >= ban_limit:
            await punish_nuker(guild, executor, f"Mass Ban Limit Reached ({ban_limit}+ Bans)")
            for member_id, ban_time in list(banned_members_history):
                if (now - ban_time).total_seconds() <= 120:
                    try:
                        ban_entry = await guild.fetch_ban(discord.Object(id=member_id))
                        await guild.unban(ban_entry.user, reason="[ANTI-NUKE] Auto Restore")
                    except Exception:
                        pass
            ban_tracker[executor.id] = []
        break


@bot.event
async def on_guild_channel_delete(channel):
    guild = channel.guild
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
        executor = entry.user
        if is_whitelisted(executor, guild):
            return

        now = datetime.now(timezone.utc)
        user_deletes = clean_tracker(channel_tracker, executor.id, 120)
        user_deletes.append(now)

        if len(user_deletes) >= channel_limit:
            await punish_nuker(guild, executor, f"Mass Channel Delete Limit Reached ({channel_limit}+ Channels)")
            channel_tracker[executor.id] = []

        try:
            new_channel = await channel.clone(reason="[ANTI-NUKE] Recreating deleted channel")
            await new_channel.edit(position=channel.position)
        except Exception as e:
            print(f"[ANTI-NUKE ERROR] {e}")
        break


# ==================== TICKET SYSTEM (BUTTONS & MODALS) ====================

class DynamicCustomModal(Modal):
    def __init__(self, title, category_name, questions):
        super().__init__(title=title[:45])
        self.category_name = category_name
        self.inputs = []

        for idx, q in enumerate(questions):
            t_input = TextInput(label=q[:45], placeholder="Type answer...", style=discord.TextStyle.paragraph, custom_id=f"dyn_q_{idx}")
            self.inputs.append((q, t_input))
            self.add_item(t_input)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user

        ticket_channel_name = f"ticket-{user.name.lower()}"
        existing_channel = discord.utils.get(guild.channels, name=ticket_channel_name)
        if existing_channel:
            await interaction.response.send_message(f"❌ Ticket already active: {existing_channel.mention}", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        global ticket_support_role_id
        support_role = guild.get_role(ticket_support_role_id) if ticket_support_role_id else None
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        ticket_channel = await guild.create_text_channel(name=ticket_channel_name, overwrites=overwrites)
        ping_text = support_role.mention if support_role else "@here"
        await ticket_channel.send(f"🔔 **New Ticket!** {ping_text} - {user.mention} needs assistance.")

        embed = discord.Embed(
            title=f"🎫 {self.category_name}",
            description=f"Welcome {user.mention}!\nOur support team will assist you shortly.",
            color=discord.Color.red()
        )
        for label, input_item in self.inputs:
            embed.add_field(name=f"📌 {label}", value=input_item.value or "N/A", inline=False)
        
        await ticket_channel.send(embed=embed, view=TicketControlView())
        await interaction.response.send_message(f"✅ Ticket created: {ticket_channel.mention}", ephemeral=True)


class DynamicCustomTicketView(View):
    def __init__(self, b1_name, b1_q, b2_name=None, b2_q=None, b3_name=None, b3_q=None):
        super().__init__(timeout=None)

        if b1_name and b1_q:
            btn1 = Button(label=b1_name, style=discord.ButtonStyle.danger, emoji="📩", custom_id="cbtn_1")
            async def b1_cb(interaction: discord.Interaction):
                q_list = [x.strip() for x in b1_q.split(",") if x.strip()]
                await interaction.response.send_modal(DynamicCustomModal(b1_name, b1_name, q_list))
            btn1.callback = b1_cb
            self.add_item(btn1)

        if b2_name and b2_q:
            btn2 = Button(label=b2_name, style=discord.ButtonStyle.primary, emoji="📌", custom_id="cbtn_2")
            async def b2_cb(interaction: discord.Interaction):
                q_list = [x.strip() for x in b2_q.split(",") if x.strip()]
                await interaction.response.send_modal(DynamicCustomModal(b2_name, b2_name, q_list))
            btn2.callback = b2_cb
            self.add_item(btn2)

        if b3_name and b3_q:
            btn3 = Button(label=b3_name, style=discord.ButtonStyle.secondary, emoji="⚙️", custom_id="cbtn_3")
            async def b3_cb(interaction: discord.Interaction):
                q_list = [x.strip() for x in b3_q.split(",") if x.strip()]
                await interaction.response.send_modal(DynamicCustomModal(b3_name, b3_name, q_list))
            btn3.callback = b3_cb
            self.add_item(btn3)


class BaseTicketModal(Modal):
    def __init__(self, title, category_name, fields):
        super().__init__(title=title)
        self.category_name = category_name
        self.inputs = []

        for f in fields:
            text_input = TextInput(
                label=f["label"],
                placeholder=f.get("placeholder", ""),
                style=f.get("style", discord.TextStyle.short),
                required=f.get("required", True)
            )
            self.inputs.append((f["label"], text_input))
            self.add_item(text_input)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user

        ticket_channel_name = f"ticket-{user.name.lower()}"
        existing_channel = discord.utils.get(guild.channels, name=ticket_channel_name)
        if existing_channel:
            await interaction.response.send_message(f"❌ Ticket already active: {existing_channel.mention}", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        global ticket_support_role_id
        support_role = guild.get_role(ticket_support_role_id) if ticket_support_role_id else None
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        ticket_channel = await guild.create_text_channel(name=ticket_channel_name, overwrites=overwrites)
        ping_text = support_role.mention if support_role else "@here"
        await ticket_channel.send(f"🔔 **New Ticket!** {ping_text} - {user.mention} needs assistance.")

        embed = discord.Embed(
            title=f"🎫 {self.category_name}",
            description=f"Welcome {user.mention}!\nOur support team will assist you shortly.",
            color=discord.Color.red()
        )
        for label, input_item in self.inputs:
            embed.add_field(name=f"📌 {label}", value=input_item.value or "N/A", inline=False)
        
        await ticket_channel.send(embed=embed, view=TicketControlView())
        await interaction.response.send_message(f"✅ Ticket created: {ticket_channel.mention}", ephemeral=True)


class TicketButtonView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Player Report", style=discord.ButtonStyle.danger, custom_id="btn_player_report", emoji="📌")
    async def player_report_btn(self, interaction: discord.Interaction, button: Button):
        fields = [
            {"label": "Your IGN", "placeholder": "In-Game Name", "style": discord.TextStyle.short},
            {"label": "Rule Breaker IGN", "placeholder": "Target player IGN", "style": discord.TextStyle.short},
            {"label": "Reason & Proof Link", "placeholder": "Proof details", "style": discord.TextStyle.paragraph}
        ]
        await interaction.response.send_modal(BaseTicketModal("📌 Player Report", "Player Report", fields))

    @discord.ui.button(label="Punishment Appeal", style=discord.ButtonStyle.danger, custom_id="btn_punishment_appeal", emoji="📌")
    async def appeal_btn(self, interaction: discord.Interaction, button: Button):
        fields = [
            {"label": "Your IGN", "placeholder": "In-Game Name", "style": discord.TextStyle.short},
            {"label": "Punishment Reason", "placeholder": "Why were you banned/muted?", "style": discord.TextStyle.short},
            {"label": "Why unban you?", "placeholder": "Reason for appeal", "style": discord.TextStyle.paragraph}
        ]
        await interaction.response.send_modal(BaseTicketModal("📌 Punishment Appeal", "Punishment Appeal", fields))

    @discord.ui.button(label="Report a Bug", style=discord.ButtonStyle.danger, custom_id="btn_bug_report", emoji="🛠️")
    async def bug_btn(self, interaction: discord.Interaction, button: Button):
        fields = [
            {"label": "Your IGN", "placeholder": "In-Game Name", "style": discord.TextStyle.short},
            {"label": "Bug Details", "placeholder": "How to recreate the glitch?", "style": discord.TextStyle.paragraph}
        ]
        await interaction.response.send_modal(BaseTicketModal("🛠️ Bug Report", "Bug Report", fields))

    @discord.ui.button(label="Other", style=discord.ButtonStyle.danger, custom_id="btn_other_ticket", emoji="💬")
    async def other_btn(self, interaction: discord.Interaction, button: Button):
        fields = [
            {"label": "Your IGN", "placeholder": "In-Game Name", "style": discord.TextStyle.short},
            {"label": "How can we help?", "placeholder": "State your query", "style": discord.TextStyle.paragraph}
        ]
        await interaction.response.send_modal(BaseTicketModal("💬 General Support", "General Support", fields))

    @discord.ui.button(label="Staff Report", style=discord.ButtonStyle.danger, custom_id="btn_staff_report", emoji="👍")
    async def staff_report_btn(self, interaction: discord.Interaction, button: Button):
        fields = [
            {"label": "Your IGN", "placeholder": "In-Game Name", "style": discord.TextStyle.short},
            {"label": "Staff Name", "placeholder": "Staff member name", "style": discord.TextStyle.short},
            {"label": "Complaint / Proof", "placeholder": "Details", "style": discord.TextStyle.paragraph}
        ]
        await interaction.response.send_modal(BaseTicketModal("👍 Staff Report", "Staff Report", fields))


class TicketControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim Ticket 🖐️", style=discord.ButtonStyle.success, custom_id="claim_ticket")
    async def claim_ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(embed=discord.Embed(description=f"✅ Ticket claimed by {interaction.user.mention}!", color=discord.Color.green()))

    @discord.ui.button(label="Close Ticket 🔒", style=discord.ButtonStyle.danger, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("🔒 Closing ticket in 5 seconds...")
        await asyncio.sleep(5)
        await interaction.channel.delete(reason="Ticket Closed")


# ==================== SLASH COMMANDS ====================

@bot.tree.command(name="setup_ticket", description="Post ticket panel with individual category buttons.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setup_ticket(interaction: discord.Interaction, role: discord.Role = None):
    global ticket_support_role_id
    if role: ticket_support_role_id = role.id

    embed = discord.Embed(
        title="Help & Support",
        description=(
            f"Hi! Welcome to the **{interaction.guild.name}** Support 🎉\n"
            "We are pleased you are here.\n"
            "Please select the option you want support for eg *Player Report, Bug Report, Punishment appeal*.\n"
            "Thanks For Supporting us 🥰"
        ),
        color=discord.Color.from_rgb(230, 230, 210)
    )
    embed.set_footer(text=f"Powered by {bot.user.name}")

    await interaction.channel.send(embed=embed, view=TicketButtonView())
    await interaction.response.send_message("✅ Button Ticket Panel created!", ephemeral=True)


@bot.tree.command(name="setup_custom_ticket", description="Post custom ticket panel with dynamic questions.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setup_custom_ticket(
    interaction: discord.Interaction,
    panel_title: str,
    btn1_label: str,
    btn1_questions: str,
    btn2_label: str = None,
    btn2_questions: str = None,
    btn3_label: str = None,
    btn3_questions: str = None
):
    embed = discord.Embed(
        title=panel_title,
        description="Select an option below to open a support ticket.",
        color=discord.Color.red()
    )
    view = DynamicCustomTicketView(btn1_label, btn1_questions, btn2_label, btn2_questions, btn3_label, btn3_questions)
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ Dynamic Custom Ticket Panel created!", ephemeral=True)


@bot.tree.command(name="set_ban_limit", description="Set anti-nuke max ban threshold limit.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_set_ban_limit(interaction: discord.Interaction, limit: int):
    global ban_limit
    ban_limit = limit
    await interaction.response.send_message(f"✅ **Anti-Nuke Ban Limit updated to:** `{ban_limit}` bans / 2 mins")


@bot.tree.command(name="set_channel_limit", description="Set anti-nuke max channel delete threshold limit.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_set_channel_limit(interaction: discord.Interaction, limit: int):
    global channel_limit
    channel_limit = limit
    await interaction.response.send_message(f"✅ **Anti-Nuke Channel Delete Limit updated to:** `{channel_limit}` channels / 2 mins")


@bot.tree.command(name="set_spam_limit", description="Set max allowed messages within 5 seconds before mute.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_set_spam_limit(interaction: discord.Interaction, messages_count: int):
    global spam_limit
    spam_limit = messages_count
    await interaction.response.send_message(f"✅ **Anti-Spam Limit updated to:** `{spam_limit}` msgs / 5 sec")


@bot.tree.command(name="antinuke_help", description="Show all Anti-Nuke and Anti-Spam configuration commands.")
async def help_antinuke(interaction: discord.Interaction):
    embed = discord.Embed(title="🛡️ Anti-Nuke & Anti-Spam Commands", color=discord.Color.red())
    embed.add_field(name="/set_ban_limit <limit>", value="Set max ban limit threshold", inline=False)
    embed.add_field(name="/set_channel_limit <limit>", value="Set max channel deletion limit", inline=False)
    embed.add_field(name="/set_spam_limit <msgs>", value="Set message spam speed threshold", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="ticket_help", description="Show all Ticket Panel management commands.")
async def help_ticket(interaction: discord.Interaction):
    embed = discord.Embed(title="🎫 Ticket System Commands", color=discord.Color.blue())
    embed.add_field(name="/setup_ticket [role]", value="Post default button panel", inline=False)
    embed.add_field(name="/setup_custom_ticket", value="Post custom categories & custom questions panel", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="welcome_help", description="Show all Welcome System commands.")
async def help_welcome(interaction: discord.Interaction):
    embed = discord.Embed(title="👋 Welcome System Commands", color=discord.Color.green())
    embed.add_field(name="/setup_welcome [channel]", value="Set welcome channel", inline=False)
    embed.add_field(name="/set_welcomemsg <msg>", value="Set custom text with tags {user},{server},{count},{inviter}", inline=False)
    embed.add_field(name="/set_welcomeimg <url>", value="Set banner GIF/Image", inline=False)
    embed.add_field(name="/disable_welcome", value="Turn off welcome system", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="invites_help", description="Show all Invite Tracker commands.")
async def help_invites(interaction: discord.Interaction):
    embed = discord.Embed(title="📊 Invite Tracker Commands", color=discord.Color.gold())
    embed.add_field(name="/setup_invitelog [channel]", value="Set invite logging channel", inline=False)
    embed.add_field(name="/invites [member]", value="Check member invite count", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="moderation_help", description="Show all Moderation commands.")
async def help_mod(interaction: discord.Interaction):
    embed = discord.Embed(title="🔨 Moderation Commands", color=discord.Color.purple())
    embed.add_field(name="/ban <member> [reason]", value="Permanently ban a member", inline=False)
    embed.add_field(name="/mute <member> <time> [reason]", value="Timeout member (e.g. 10m, 1h)", inline=False)
    embed.add_field(name="/purge <amount>", value="Clear up to 100 messages in channel", inline=False)
    embed.add_field(name="/dmall <message>", value="Broadcast announcement via DMs", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="setup_invitelog", description="Set channel for invite logs.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setup_invitelog(interaction: discord.Interaction, channel: discord.TextChannel = None):
    global invite_log_channel_id
    target = channel or interaction.channel
    invite_log_channel_id = target.id
    await interaction.response.send_message(f"✅ **Invite Logger set to:** {target.mention}")


@bot.tree.command(name="invites", description="Check invite stats of a server member.")
async def slash_invites(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    total_uses = 0
    try:
        guild_invites = await interaction.guild.invites()
        for inv in guild_invites:
            if inv.inviter and inv.inviter.id == target.id:
                total_uses += inv.uses
    except Exception:
        pass

    embed = discord.Embed(
        title=f"📊 Invite Stats: {target.display_name}",
        description=f"👤 **Member:** {target.mention}\n📈 **Total Invites:** `{total_uses}`",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="dmall", description="Send DM announcement to all server members.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_dmall(interaction: discord.Interaction, message: str):
    await interaction.response.send_message("⏳ **Starting DM Broadcast...** Safe delay active.")
    success_count, failed_count = 0, 0
    embed = discord.Embed(title=f"📢 Announcement from {interaction.guild.name}", description=message, color=discord.Color.gold())

    for member in interaction.guild.members:
        if member.bot: continue
        try:
            await member.send(embed=embed)
            success_count += 1
            await asyncio.sleep(1.5)
        except Exception:
            failed_count += 1

    await interaction.followup.send(f"✅ Sent: {success_count} | ❌ Failed: {failed_count}")


@bot.tree.command(name="setup_welcome", description="Set welcome channel.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setup_welcome(interaction: discord.Interaction, channel: discord.TextChannel = None):
    global welcome_channel_id, welcome_enabled
    target = channel or interaction.channel
    welcome_channel_id = target.id
    welcome_enabled = True
    await interaction.response.send_message(f"✅ **Welcome channel set to:** {target.mention}")


@bot.tree.command(name="set_welcomemsg", description="Set custom welcome text.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_set_welcomemsg(interaction: discord.Interaction, message: str):
    global custom_welcome_msg
    custom_welcome_msg = message
    await interaction.response.send_message("✅ **Custom Welcome Message Set!**")


@bot.tree.command(name="set_welcomeimg", description="Set banner image URL.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_set_welcomeimg(interaction: discord.Interaction, url: str):
    global custom_welcome_img
    custom_welcome_img = url
    await interaction.response.send_message(f"✅ **Image Set:** {url}")


@bot.tree.command(name="disable_welcome", description="Disable welcome messages.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_disable_welcome(interaction: discord.Interaction):
    global welcome_enabled
    welcome_enabled = False
    await interaction.response.send_message("🚫 **Welcome messages DISABLED!**")


@bot.tree.command(name="ban", description="Ban a member.")
@app_commands.checks.has_permissions(ban_members=True)
async def slash_ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if is_whitelisted(member, interaction.guild):
        await interaction.response.send_message("❌ **Access Denied:** User is whitelisted.", ephemeral=True)
        return
    await member.ban(reason=reason)
    await interaction.response.send_message(f"🔨 **{member.mention} banned!**")


@bot.tree.command(name="mute", description="Timeout a member.")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_mute(interaction: discord.Interaction, member: discord.Member, duration: str, reason: str = "No reason provided"):
    if is_whitelisted(member, interaction.guild):
        await interaction.response.send_message("❌ **Access Denied:** User is whitelisted.", ephemeral=True)
        return
    seconds = parse_time(duration)
    await member.timeout(timedelta(seconds=seconds), reason=reason)
    await interaction.response.send_message(f"🤐 **{member.mention} timed out for {duration}!**")


@bot.tree.command(name="purge", description="Bulk delete messages in current channel.")
@app_commands.checks.has_permissions(manage_messages=True)
async def slash_purge(interaction: discord.Interaction, amount: int):
    if amount < 1 or amount > 100:
        await interaction.response.send_message("❌ Please specify an amount between 1 and 100.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"🧹 Successfully deleted **{len(deleted)}** messages!", ephemeral=True)


# ==================== WELCOME & INVITE EVENT LOGIC ====================

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
        inviter_name = inviter_user.name if inviter_user else "Unknown"
        if custom_welcome_msg:
            description_text = custom_welcome_msg.format(
                user=member.mention,
                server=guild.name,
                count=guild.member_count,
                inviter=inviter_name
            )
        else:
            description_text = f"Hey {member.mention}, welcome to **{guild.name}**!"

        embed = discord.Embed(title="👋 Welcome!", description=description_text, color=discord.Color.blue())
        embed.set_thumbnail(url=member.display_avatar.url)
        if custom_welcome_img:
            embed.set_image(url=custom_welcome_img)
        embed.set_footer(text=f"Member #{guild.member_count}")
        await target_channel.send(content=f"Welcome {member.mention}!", embed=embed)


# ==================== RUN BOT ====================

keep_alive()
BOT_TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(BOT_TOKEN)
