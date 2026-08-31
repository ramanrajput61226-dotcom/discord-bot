import os
import random
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Modal, TextInput

# Try importing keep_alive if running on Replit or similar hosting services
try:
    from keep_alive import keep_alive
except ImportError:
    def keep_alive():
        pass


# ==============================================================================
# GLOBAL STATES & CONFIGURATIONS
# ==============================================================================

OWNER_ID = 1255544682759323680  # Safely initialized to prevent NameError
ban_limit = 5
channel_limit = 3
spam_limit = 5
custom_prefix = "!"

ticket_log_channel_id = None
custom_ticket_ping = "{role}"

# Fully Customizable Ticket Configurations Dictionary per Guild:
# { guild_id: { "category_id": int, "role_id": int, "title": str, "desc": str, "buttons": {custom_id: {"label": str, "questions": [str]}} } }
ticket_configs = {}

invite_log_channel_id = None
welcome_channel_id = None
custom_welcome_msg = None
custom_welcome_img = None
welcome_enabled = False

invites_cache = {}

# Message tracking dictionary for spam detection: {user_id: [timestamps]}
spam_tracking = {}

# Active nuke tracking dictionary: {user_id: {'bans': [timestamps], 'channels': [timestamps]}}
nuke_tracking = {}


# ==============================================================================
# BOT INITIALIZATION & INTENTS
# ==============================================================================

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


# ==============================================================================
# HELPER FUNCTIONS & CHECKS
# ==============================================================================

def is_whitelisted(user: discord.Member, guild: discord.Guild) -> bool:
    """Check if a user is the bot owner or has administrator permissions."""
    if OWNER_ID and user.id == OWNER_ID:
        return True
    if user.guild_permissions.administrator:
        return True
    return False


def parse_time(duration_str: str) -> int:
    """Parse duration string like 10s, 5m, 2h, 1d into total seconds."""
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    unit = duration_str[-1].lower()
    val = duration_str[:-1]
    if unit in multipliers and val.isdigit():
        return int(val) * multipliers[unit]
    return 60  # Default fallback 60 seconds


# ==============================================================================
# BOT EVENTS: ON_READY & CACHING
# ==============================================================================

@bot.event
async def on_ready():
    print(f"==================================================")
    print(f" Logged in successfully as {bot.user}")
    print(f" Bot ID: {bot.user.id}")
    print(f"==================================================")

    # Sync slash commands globally or per guild
    try:
        synced = await bot.tree.sync()
        print(f" [Slash Commands] Successfully synced {len(synced)} command(s).")
    except Exception as e:
        print(f" [Slash Commands] Failed to sync commands: {e}")

    # Cache invites for all guilds to track invite counts accurately
    for guild in bot.guilds:
        try:
            invites_cache[guild.id] = await guild.invites()
            print(f" [Invite Cache] Cached invites for guild: {guild.name}")
        except Exception as e:
            print(f" [Invite Cache] Could not cache invites for {guild.name}: {e}")

    print(f"==================================================")


# ==============================================================================
# HYBRID PREFIX & SLASH COMMAND WRAPPER
# ==============================================================================

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Check for anti-spam heuristics
    user_id = message.author.id
    guild = message.guild

    if guild and not is_whitelisted(message.author, guild):
        current_time = datetime.now().timestamp()
        if user_id not in spam_tracking:
            spam_tracking[user_id] = []

        # Filter timestamps within the last 5 seconds
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

    # Process traditional prefix commands if message starts with prefix
    if message.content.startswith(custom_prefix):
        ctx = await bot.get_context(message)
        if ctx.command:
            await bot.invoke(ctx)
            return

    await bot.process_commands(message)


# ==============================================================================
# ANTI-NUKE MONITORING EVENTS
# ==============================================================================

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

            # Clean up old timestamps (2 minutes window)
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

            # Clean up old timestamps (2 minutes window)
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


# ==============================================================================
# ADVANCED FULLY CUSTOMIZABLE TICKET SYSTEM
# ==============================================================================

class DynamicTicketModal(Modal):
    def __init__(self, button_id: str, button_label: str, questions: list):
        super().__init__(title=f"{button_label} Ticket Form")
        self.button_id = button_id
        self.button_label = button_label
        self.question_inputs = []

        for q in questions:
            truncated_label = q[:45] + "..." if len(q) > 48 else q
            text_input = TextInput(
                label=truncated_label,
                placeholder="Type your answer here...",
                style=discord.TextStyle.paragraph,
                required=True,
                max_length=500
            )
            self.question_inputs.append((q, text_input))
            self.add_item(text_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user = interaction.user
        config = ticket_configs.get(guild.id, {})

        category_id = config.get("category_id")
        category = guild.get_channel(category_id) if category_id else None

        role_id = config.get("role_id")
        support_role = guild.get_role(role_id) if role_id else None

        # Create overwrites
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
        }
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        ticket_channel_name = f"ticket-{user.name}".lower().replace(" ", "-")
        ticket_channel = await guild.create_text_channel(
            name=ticket_channel_name,
            category=category,
            overwrites=overwrites,
            reason=f"Support ticket opened by {user}"
        )

        embed = discord.Embed(
            title=f"🎫 {self.button_label} Ticket",
            description=f"Ticket opened by {user.mention}\nPlease wait patiently for staff assistance.",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )

        for q, text_input in self.question_inputs:
            embed.add_field(name=q, value=text_input.value, inline=False)

        embed.set_footer(text=f"User ID: {user.id}")

        close_view = TicketControlView()
        
        # Format ping message
        role_mention = support_role.mention if support_role else "@here"
        ping_content = custom_ticket_ping.replace("{user}", user.mention).replace("{role}", role_mention)

        await ticket_channel.send(content=ping_content, embed=embed, view=close_view)
        await interaction.followup.send(f"✅ Your ticket has been created successfully: {ticket_channel.mention}", ephemeral=True)


class DynamicTicketButtonView(discord.ui.View):
    def __init__(self, buttons_data: dict):
        super().__init__(timeout=None)
        for custom_id, data in buttons_data.items():
            self.add_item(DynamicTicketButton(custom_id, data["label"], data["questions"]))

class DynamicTicketButton(discord.ui.Button):
    def __init__(self, custom_id: str, label: str, questions: list):
        super().__init__(style=discord.ButtonStyle.primary, label=label, custom_id=custom_id)
        self.questions = questions

    async def callback(self, interaction: discord.Interaction):
        modal = DynamicTicketModal(self.custom_id, self.label, self.questions)
        await interaction.response.send_modal(modal)


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="close_ticket_btn")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔒 Ticket closing in 5 seconds...", ephemeral=False)
        
        if ticket_log_channel_id:
            log_chan = interaction.guild.get_channel(ticket_log_channel_id)
            if log_chan:
                try:
                    messages = [msg async for msg in interaction.channel.history(limit=100, oldest_first=True)]
                    transcript_content = f"Transcript for #{interaction.channel.name} (Closed by {interaction.user})\n\n"
                    for m in messages:
                        transcript_content += f"[{m.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {m.author}: {m.content}\n"
                    
                    file_path = f"transcript_{interaction.channel.id}.txt"
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(transcript_content)

                    await log_chan.send(
                        f"📁 **Transcript for closed ticket:** `#{interaction.channel.name}`",
                        file=discord.File(file_path)
                    )
                    os.remove(file_path)
                except Exception as e:
                    print(f"Failed to generate transcript: {e}")

        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")
        except Exception:
            pass


# Wizard Modal for Setup Ticket Configuration
class TicketSetupWizardModal(Modal):
    def __init__(self, category: discord.CategoryChannel, role: discord.Role):
        super().__init__(title="Configure Ticket Panel Details")
        self.category = category
        self.role = role

        self.panel_title = TextInput(
            label="Panel Title",
            default="Support Ticket Panel",
            max_length=100
        )
        self.panel_desc = TextInput(
            label="Panel Description",
            style=discord.TextStyle.paragraph,
            default="Click a button below to open a support ticket.",
            max_length=500
        )
        self.add_item(self.panel_title)
        self.add_item(self.panel_desc)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        
        # Default questions/buttons if user wants a starting point, fully editable via config commands
        default_buttons = {
            "ticket_btn_1": {
                "label": "Player Report",
                "questions": [
                    "What is the username/ID of the player?",
                    "What rule did they break?",
                    "Provide evidence links:"
                ]
            },
            "ticket_btn_2": {
                "label": "General Support",
                "questions": [
                    "Describe your issue in detail:"
                ]
            }
        }

        ticket_configs[guild.id] = {
            "category_id": self.category.id,
            "role_id": self.role.id,
            "title": self.panel_title.value,
            "desc": self.panel_desc.value,
            "buttons": default_buttons
        }

        embed = discord.Embed(
            title=self.panel_title.value,
            description=self.panel_desc.value,
            color=discord.Color.blue()
        )
        view = DynamicTicketButtonView(default_buttons)
        
        await interaction.channel.send(embed=embed, view=view)
        await interaction.response.send_message("✅ **Ticket Panel deployed successfully with your custom configuration!** Use `/edit_ticket` to update buttons/questions anytime.", ephemeral=True)


class TicketSetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.select(cls=discord.ui.ChannelSelect, placeholder="1️⃣ Select Category where tickets will be created...", channel_types=[discord.ChannelType.category], min_values=1, max_values=1)
    async def select_category(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        self.selected_category = select.values[0]
        await interaction.response.send_message(f"✅ Category selected: **{self.selected_category.name}**. Now select the Support Role using the dropdown below.", ephemeral=True)

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="2️⃣ Select Support Role to ping inside tickets...", min_values=1, max_values=1)
    async def select_role(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        if not hasattr(self, 'selected_category'):
            await interaction.response.send_message("❌ Please select a Category first!", ephemeral=True)
            return
        selected_role = select.values[0]
        await interaction.response.send_modal(TicketSetupWizardModal(self.selected_category, selected_role))


@bot.hybrid_command(name="setup_ticket", description="Interactive setup wizard for fully customizable tickets.")
@app_commands.checks.has_permissions(administrator=True)
async def setup_ticket(ctx: commands.Context):
    view = TicketSetupView()
    await ctx.send("📌 **Ticket Setup Wizard:** Please select the target Category and Support Role below.", view=view, ephemeral=True)


@bot.hybrid_command(name="add_ticket_button", description="Add a new custom button and questions to your ticket panel.")
@app_commands.describe(button_name="Name of the ticket button", question_1="First question to ask", question_2="Optional second question", question_3="Optional third question")
@app_commands.checks.has_permissions(administrator=True)
async def add_ticket_button(ctx: commands.Context, button_name: str, question_1: str, question_2: str = None, question_3: str = None):
    guild = ctx.guild
    if guild.id not in ticket_configs:
        await ctx.send("❌ Please run `/setup_ticket` first to initialize panel settings.", ephemeral=True)
        return

    questions = [q for q in [question_1, question_2, question_3] if q]
    custom_id = f"custom_ticket_{random.randint(1000, 9999)}"
    
    ticket_configs[guild.id]["buttons"][custom_id] = {
        "label": button_name,
        "questions": questions
    }
    await ctx.send(f"✅ Successfully added new ticket button **{button_name}** with `{len(questions)}` questions! Re-send your panel using `/setup_ticket` or update it.", ephemeral=True)


# ==============================================================================
# GIVEAWAY SYSTEM WITH FIXED / CUSTOM WINNER LOGIC
# ==============================================================================

@bot.hybrid_command(name="giveaway", description="Start an interactive giveaway with optional fixed winner.")
@app_commands.describe(
    prize="The prize being given away",
    duration="Duration format (e.g. 30s, 10m, 2h, 1d)",
    winners_count="Number of winners to pick",
    fixed_winner="Optional specific member to guarantee as a winner"
)
@commands.has_permissions(manage_guild=True)
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


# ==============================================================================
# CONFIGURATION COMMANDS (ANTI-NUKE, PREFIX, LIMITS)
# ==============================================================================

@bot.hybrid_command(name="set_ban_limit", description="Set anti-nuke max ban threshold limit.")
@app_commands.checks.has_permissions(administrator=True)
async def set_ban_limit(ctx: commands.Context, limit: int):
    global ban_limit
    ban_limit = limit
    res = f"✅ **Anti-Nuke Ban Limit updated to:** `{ban_limit}` bans / 2 mins"
    await ctx.send(res)


@bot.hybrid_command(name="set_channel_limit", description="Set anti-nuke max channel delete threshold limit.")
@app_commands.checks.has_permissions(administrator=True)
async def set_channel_limit(ctx: commands.Context, limit: int):
    global channel_limit
    channel_limit = limit
    await ctx.send(f"✅ **Anti-Nuke Channel Delete Limit updated to:** `{channel_limit}` channels / 2 mins")


@bot.hybrid_command(name="set_spam_limit", description="Set max allowed messages within 5 seconds before mute.")
@app_commands.checks.has_permissions(administrator=True)
async def set_spam_limit(ctx: commands.Context, messages_count: int):
    global spam_limit
    spam_limit = messages_count
    await ctx.send(f"✅ **Anti-Spam Limit updated to:** `{spam_limit}` msgs / 5 sec")


@bot.hybrid_command(name="set_prefix", description="Set custom prefix for text commands.")
@app_commands.checks.has_permissions(administrator=True)
async def set_prefix(ctx: commands.Context, prefix: str):
    global custom_prefix
    custom_prefix = prefix
    bot.command_prefix = commands.when_mentioned_or(custom_prefix)
    await ctx.send(f"✅ **Custom Prefix updated to:** `{custom_prefix}`")


@bot.hybrid_command(name="ticket_log_channel", description="Set log channel for closed ticket transcripts.")
@app_commands.checks.has_permissions(administrator=True)
async def ticket_log_channel(ctx: commands.Context, channel: discord.TextChannel):
    global ticket_log_channel_id
    ticket_log_channel_id = channel.id
    await ctx.send(f"✅ **Ticket Transcript Log Channel set to:** {channel.mention}")


@bot.hybrid_command(name="set_ticket_ping", description="Customize ticket opening ping message.")
@app_commands.checks.has_permissions(administrator=True)
async def set_ticket_ping(ctx: commands.Context, message: str):
    global custom_ticket_ping
    custom_ticket_ping = message
    await ctx.send(f"✅ **Ticket Open Ping updated!**\nFormat: `{message}`")


# ==============================================================================
# WELCOME SYSTEM & INVITE TRACKER COMMANDS
# ==============================================================================

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
@app_commands.checks.has_permissions(administrator=True)
async def setup_welcome(ctx: commands.Context):
    view = WelcomeSelectView()
    msg = "📌 **Please select your welcome channel from the dropdown below:**"
    await ctx.send(msg, view=view, ephemeral=True)


@bot.hybrid_command(name="disable_welcome", description="Turn off welcome system.")
@app_commands.checks.has_permissions(administrator=True)
async def disable_welcome(ctx: commands.Context):
    global welcome_enabled
    welcome_enabled = False
    await ctx.send("❌ **Welcome system has been disabled.**")


@bot.hybrid_command(name="setup_invitelog", description="Set channel for invite logs.")
@app_commands.checks.has_permissions(administrator=True)
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


# ==============================================================================
# MODERATION TOOLS & BROADCAST COMMANDS
# ==============================================================================

@bot.hybrid_command(name="ban", description="Permanently ban a member.")
@app_commands.describe(member="The member to ban", reason="Reason for the ban")
@app_commands.checks.has_permissions(ban_members=True)
async def ban_cmd(ctx: commands.Context, member: discord.Member, reason: str = "No reason provided"):
    guild = ctx.guild
    if is_whitelisted(member, guild):
        await ctx.send("❌ **Access Denied:** User is whitelisted.", ephemeral=True)
        return

    await member.ban(reason=reason)
    await ctx.send(f"🔨 **{member.mention} banned successfully!**")


@bot.hybrid_command(name="mute", description="Timeout a member for a specified duration.")
@app_commands.describe(member="The member to mute", duration="Duration (e.g. 10m, 1h)", reason="Reason for timeout")
@app_commands.checks.has_permissions(moderate_members=True)
async def mute_cmd(ctx: commands.Context, member: discord.Member, duration: str, reason: str = "No reason provided"):
    guild = ctx.guild
    if is_whitelisted(member, guild):
        await ctx.send("❌ **Access Denied:** User is whitelisted.", ephemeral=True)
        return

    seconds = parse_time(duration)
    await member.timeout(timedelta(seconds=seconds), reason=reason)
    await ctx.send(f"🤐 **{member.mention} timed out for {duration}!**")


@bot.hybrid_command(name="purge", description="Bulk delete messages in current channel.")
@app_commands.describe(amount="Number of messages to delete (1-100)")
@app_commands.checks.has_permissions(manage_messages=True)
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
@app_commands.checks.has_permissions(manage_roles=True)
async def role_cmd(ctx: commands.Context, action: Literal["add", "remove"], member: discord.Member, role: discord.Role):
    user = ctx.author
    if action == "add":
        await member.add_roles(role, reason=f"Managed by {user}")
        res = f"✅ Successfully added **{role.name}** to {member.mention}!"
    else:
        await member.remove_roles(role, reason=f"Managed by {user}")
        res = f"✅ Successfully removed **{role.name}** from {member.mention}!"

    await ctx.send(res)


@bot.hybrid_command(name="dmall", description="Send DM announcement to all server members.")
@app_commands.describe(message="The message you want to broadcast", as_embed="True for Embed format, False for Plain Text")
@app_commands.checks.has_permissions(administrator=True)
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


# ==============================================================================
# MEMBER JOIN EVENT: WELCOME & INVITE TRACKER EXECUTION
# ==============================================================================

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


# ==============================================================================
# HELP COMMANDS MODULE
# ==============================================================================

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


# ==============================================================================
# KEEP ALIVE & BOT START EXECUTION
# ==============================================================================

keep_alive()

BOT_TOKEN = os.getenv("DISCORD_TOKEN")
if BOT_TOKEN:
    bot.run(BOT_TOKEN)
else:
    print("❌ Error: DISCORD_TOKEN environment variable not found!")


