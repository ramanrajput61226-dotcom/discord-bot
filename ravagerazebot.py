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

ban_limit = 5
channel_limit = 3
spam_limit = 5
custom_prefix = "!"

ticket_log_channel_id = None
custom_ticket_ping = "{role}"

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
    if user.id == OWNER_ID:
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
# ADVANCED TICKET SYSTEM WITH DROPDOWNS & MODALS
# ==============================================================================

class TicketModal(Modal):
    def __init__(self, ticket_type: str, questions: list):
        super().__init__(title=f"{ticket_type} Ticket Form")
        self.ticket_type = ticket_type
        self.question_inputs = []

        for idx, q in enumerate(questions):
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

        # Create overwrites for ticket channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
        }

        # Attempt to put in category if any exists
        category = interaction.channel.category if isinstance(interaction.channel, discord.TextChannel) else None

        ticket_channel_name = f"ticket-{user.name}".lower().replace(" ", "-")
        ticket_channel = await guild.create_text_channel(
            name=ticket_channel_name,
            category=category,
            overwrites=overwrites,
            reason=f"Support ticket opened by {user}"
        )

        # Build responses embed
        embed = discord.Embed(
            title=f"🎫 {self.ticket_type} Ticket",
            description=f"Ticket opened by {user.mention}\nPlease wait patiently for staff assistance.",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )

        for q, text_input in self.question_inputs:
            embed.add_field(name=q, value=text_input.value, inline=False)

        embed.set_footer(text=f"User ID: {user.id}")

        close_view = TicketControlView()
        ping_content = custom_ticket_ping.replace("{user}", user.mention).replace("{role}", "@here")

        await ticket_channel.send(content=ping_content, embed=embed, view=close_view)
        await interaction.followup.send(f"✅ Your ticket has been created successfully: {ticket_channel.mention}", ephemeral=True)


class TicketDropdown(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="Player Report",
                description="Report a player for rule-breaking or misconduct",
                emoji="⚖️",
                value="Player Report"
            ),
            discord.SelectOption(
                label="Bug Report",
                description="Report a server glitch, bug, or exploit",
                emoji="🐛",
                value="Bug Report"
            )
        ]
        super().__init__(placeholder="Select ticket category...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        if selected == "Player Report":
            questions = [
                "What is the username/ID of the player you are reporting?",
                "What rule did they break or what did they do?",
                "Provide evidence (imgur links, video clips, screenshots):"
            ]
        elif selected == "Bug Report":
            questions = [
                "Where did you encounter the bug?",
                "Describe how the bug occurred step-by-step:",
                "Can you replicate this bug consistently?"
            ]
        else:
            questions = ["Describe your issue in detail:"]

        modal = TicketModal(ticket_type=selected, questions=questions)
        await interaction.response.send_modal(modal)


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketDropdown())


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="close_ticket_btn")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔒 Ticket closing in 5 seconds...", ephemeral=False)
        
        # Transcript logging logic if log channel is configured
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

@bot.tree.command(name="set_ban_limit", description="Set anti-nuke max ban threshold limit.")
@bot.command(name="set_ban_limit")
@app_commands.checks.has_permissions(administrator=True)
async def set_ban_limit(ctx_or_interaction, limit: int):
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    user = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
    guild = ctx_or_interaction.guild

    if not is_whitelisted(user, guild):
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else: await ctx_or_interaction.send(msg)
        return

    global ban_limit
    ban_limit = limit
    res = f"✅ **Anti-Nuke Ban Limit updated to:** `{ban_limit}` bans / 2 mins"
    if is_interaction: await ctx_or_interaction.response.send_message(res)
    else: await ctx_or_interaction.send(res)


@bot.tree.command(name="set_channel_limit", description="Set anti-nuke max channel delete threshold limit.")
@bot.command(name="set_channel_limit")
@app_commands.checks.has_permissions(administrator=True)
async def set_channel_limit(ctx_or_interaction, limit: int):
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    user = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
    guild = ctx_or_interaction.guild

    if not is_whitelisted(user, guild):
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else: await ctx_or_interaction.send(msg)
        return

    global channel_limit
    channel_limit = limit
    res = f"✅ **Anti-Nuke Channel Delete Limit updated to:** `{channel_limit}` channels / 2 mins"
    if is_interaction: await ctx_or_interaction.response.send_message(res)
    else: await ctx_or_interaction.send(res)


@bot.tree.command(name="set_spam_limit", description="Set max allowed messages within 5 seconds before mute.")
@bot.command(name="set_spam_limit")
@app_commands.checks.has_permissions(administrator=True)
async def set_spam_limit(ctx_or_interaction, messages_count: int):
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    user = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
    guild = ctx_or_interaction.guild

    if not is_whitelisted(user, guild):
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else: await ctx_or_interaction.send(msg)
        return

    global spam_limit
    spam_limit = messages_count
    res = f"✅ **Anti-Spam Limit updated to:** `{spam_limit}` msgs / 5 sec"
    if is_interaction: await ctx_or_interaction.response.send_message(res)
    else: await ctx_or_interaction.send(res)


@bot.tree.command(name="set_prefix", description="Set custom prefix for text commands.")
@bot.command(name="set_prefix")
@app_commands.checks.has_permissions(administrator=True)
async def set_prefix(ctx_or_interaction, prefix: str):
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    user = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
    guild = ctx_or_interaction.guild

    if not is_whitelisted(user, guild):
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else: await ctx_or_interaction.send(msg)
        return

    global custom_prefix
    custom_prefix = prefix
    bot.command_prefix = commands.when_mentioned_or(custom_prefix)
    res = f"✅ **Custom Prefix updated to:** `{custom_prefix}`"
    if is_interaction: await ctx_or_interaction.response.send_message(res)
    else: await ctx_or_interaction.send(res)


@bot.tree.command(name="ticket_log_channel", description="Set log channel for closed ticket transcripts.")
@bot.command(name="ticket_log_channel")
@app_commands.checks.has_permissions(administrator=True)
async def ticket_log_channel(ctx_or_interaction, channel: discord.TextChannel):
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    user = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
    guild = ctx_or_interaction.guild

    if not is_whitelisted(user, guild):
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else: await ctx_or_interaction.send(msg)
        return

    global ticket_log_channel_id
    ticket_log_channel_id = channel.id
    res = f"✅ **Ticket Transcript Log Channel set to:** {channel.mention}"
    if is_interaction: await ctx_or_interaction.response.send_message(res)
    else: await ctx_or_interaction.send(res)


@bot.tree.command(name="set_ticket_ping", description="Customize ticket opening ping message.")
@bot.command(name="set_ticket_ping")
@app_commands.checks.has_permissions(administrator=True)
async def set_ticket_ping(ctx_or_interaction, message: str):
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    user = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
    guild = ctx_or_interaction.guild

    if not is_whitelisted(user, guild):
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else: await ctx_or_interaction.send(msg)
        return

    global custom_ticket_ping
    custom_ticket_ping = message
    res = f"✅ **Ticket Open Ping updated!**\nFormat: `{message}`"
    if is_interaction: await ctx_or_interaction.response.send_message(res)
    else: await ctx_or_interaction.send(res)


@bot.tree.command(name="setup_ticket", description="Send ticket panel interface with dropdown menu.")
@bot.command(name="setup_ticket")
@app_commands.checks.has_permissions(administrator=True)
async def setup_ticket(ctx_or_interaction):
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    user = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
    guild = ctx_or_interaction.guild

    if not is_whitelisted(user, guild):
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else: await ctx_or_interaction.send(msg)
        return

    embed = discord.Embed(
        title="support Ticket Panel",
        description="Select an option from the dropdown menu below to open a ticket.",
        color=discord.Color.blue()
    )
    view = TicketView()
    if is_interaction:
        await ctx_or_interaction.response.send_message(embed=embed, view=view)
    else:
        await ctx_or_interaction.send(embed=embed, view=view)


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


@bot.tree.command(name="setup_welcome", description="Configure custom welcome channel, message and banner.")
@bot.command(name="setup_welcome")
@app_commands.checks.has_permissions(administrator=True)
async def setup_welcome(ctx_or_interaction):
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    user = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
    guild = ctx_or_interaction.guild

    if not is_whitelisted(user, guild):
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else: await ctx_or_interaction.send(msg)
        return

    view = WelcomeSelectView()
    msg = "📌 **Please select your welcome channel from the dropdown below:**"
    if is_interaction:
        await ctx_or_interaction.response.send_message(msg, view=view, ephemeral=True)
    else:
        await ctx_or_interaction.send(msg, view=view)


@bot.tree.command(name="disable_welcome", description="Turn off welcome system.")
@bot.command(name="disable_welcome")
@app_commands.checks.has_permissions(administrator=True)
async def disable_welcome(ctx_or_interaction):
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    user = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
    guild = ctx_or_interaction.guild

    if not is_whitelisted(user, guild):
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else: await ctx_or_interaction.send(msg)
        return

    global welcome_enabled
    welcome_enabled = False
    res = "❌ **Welcome system has been disabled.**"
    if is_interaction: await ctx_or_interaction.response.send_message(res)
    else: await ctx_or_interaction.send(res)


@bot.tree.command(name="setup_invitelog", description="Set channel for invite logs.")
@bot.command(name="setup_invitelog")
@app_commands.checks.has_permissions(administrator=True)
async def setup_invitelog(ctx_or_interaction, channel: discord.TextChannel = None):
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    user = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
    guild = ctx_or_interaction.guild

    if not is_whitelisted(user, guild):
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else: await ctx_or_interaction.send(msg)
        return

    global invite_log_channel_id
    target = channel or (ctx_or_interaction.channel if is_interaction else ctx_or_interaction.message.channel)
    invite_log_channel_id = target.id
    res = f"✅ **Invite Logger set to:** {target.mention}"
    if is_interaction: await ctx_or_interaction.response.send_message(res)
    else: await ctx_or_interaction.send(res)


@bot.tree.command(name="invites", description="Check invite stats of a server member.")
@bot.command(name="invites")
async def invites(ctx_or_interaction, member: discord.Member = None):
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    target = member or (ctx_or_interaction.user if is_interaction else ctx_or_interaction.author)
    guild = ctx_or_interaction.guild

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
    if is_interaction:
        await ctx_or_interaction.response.send_message(embed=embed)
    else:
        await ctx_or_interaction.send(embed=embed)


# ==============================================================================
# MODERATION TOOLS & BROADCAST COMMANDS
# ==============================================================================

@bot.tree.command(name="ban", description="Permanently ban a member.")
@bot.command(name="ban")
@app_commands.checks.has_permissions(ban_members=True)
async def ban_cmd(ctx_or_interaction, member: discord.Member, reason: str = "No reason provided"):
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    user = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
    guild = ctx_or_interaction.guild

    if not is_whitelisted(user, guild) and not user.guild_permissions.ban_members:
        msg = "❌ **Access Denied:** You lack ban permissions."
        if is_interaction: await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else: await ctx_or_interaction.send(msg)
        return
    if is_whitelisted(member, guild):
        msg = "❌ **Access Denied:** User is whitelisted."
        if is_interaction: await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else: await ctx_or_interaction.send(msg)
        return

    await member.ban(reason=reason)
    res = f"🔨 **{member.mention} banned successfully!**"
    if is_interaction: await ctx_or_interaction.response.send_message(res)
    else: await ctx_or_interaction.send(res)


@bot.tree.command(name="mute", description="Timeout a member for a specified duration.")
@bot.command(name="mute")
@app_commands.checks.has_permissions(moderate_members=True)
async def mute_cmd(ctx_or_interaction, member: discord.Member, duration: str, reason: str = "No reason provided"):
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    user = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
    guild = ctx_or_interaction.guild

    if not is_whitelisted(user, guild) and not user.guild_permissions.moderate_members:
        msg = "❌ **Access Denied:** You lack timeout permissions."
        if is_interaction: await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else: await ctx_or_interaction.send(msg)
        return
    if is_whitelisted(member, guild):
        msg = "❌ **Access Denied:** User is whitelisted."
        if is_interaction: await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else: await ctx_or_interaction.send(msg)
        return

    seconds = parse_time(duration)
    await member.timeout(timedelta(seconds=seconds), reason=reason)
    res = f"🤐 **{member.mention} timed out for {duration}!**"
    if is_interaction: await ctx_or_interaction.response.send_message(res)
    else: await ctx_or_interaction.send(res)


@bot.tree.command(name="purge", description="Bulk delete messages in current channel.")
@bot.command(name="purge")
@app_commands.checks.has_permissions(manage_messages=True)
async def purge_cmd(ctx_or_interaction, amount: int):
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    user = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
    guild = ctx_or_interaction.guild
    channel = ctx_or_interaction.channel if is_interaction else ctx_or_interaction.message.channel

    if not is_whitelisted(user, guild) and not user.guild_permissions.manage_messages:
        msg = "❌ **Access Denied:** You lack message management permissions."
        if is_interaction: await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else: await ctx_or_interaction.send(msg)
        return
    if amount < 1 or amount > 100:
        msg = "❌ Please specify an amount between 1 and 100."
        if is_interaction: await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else: await ctx_or_interaction.send(msg)
        return

    if is_interaction:
        await ctx_or_interaction.response.defer(ephemeral=True)
        deleted = await channel.purge(limit=amount)
        await ctx_or_interaction.followup.send(f"🧹 Successfully deleted **{len(deleted)}** messages!", ephemeral=True)
    else:
        deleted = await channel.purge(limit=amount)
        await ctx_or_interaction.send(f"🧹 Successfully deleted **{len(deleted)}** messages!", delete_after=5)


@bot.tree.command(name="role", description="Assign or remove a role from a member easily.")
@bot.command(name="role")
@app_commands.checks.has_permissions(manage_roles=True)
async def role_cmd(ctx_or_interaction, action: Literal["add", "remove"], member: discord.Member, role: discord.Role):
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    user = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
    guild = ctx_or_interaction.guild

    if not is_whitelisted(user, guild) and not user.guild_permissions.manage_roles:
        msg = "❌ **Access Denied:** You lack role management permissions."
        if is_interaction: await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else: await ctx_or_interaction.send(msg)
        return

    if action == "add":
        await member.add_roles(role, reason=f"Managed by {user}")
        res = f"✅ Successfully added **{role.name}** to {member.mention}!"
    else:
        await member.remove_roles(role, reason=f"Managed by {user}")
        res = f"✅ Successfully removed **{role.name}** from {member.mention}!"

    if is_interaction: await ctx_or_interaction.response.send_message(res)
    else: await ctx_or_interaction.send(res)


@bot.tree.command(name="dmall", description="Send DM announcement to all server members.")
@app_commands.describe(message="The message you want to broadcast", as_embed="True for Embed format, False for Plain Text")
@bot.command(name="dmall")
@app_commands.checks.has_permissions(administrator=True)
async def dmall(ctx_or_interaction, message: str, as_embed: bool = False):
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    user = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
    guild = ctx_or_interaction.guild

    if not is_whitelisted(user, guild):
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else: await ctx_or_interaction.send(msg)
        return
        
    format_type = "Embed" if as_embed else "Plain Text"
    start_msg = f"⏳ **Starting DM Broadcast ({format_type})...** Safe delay active."
    if is_interaction:
        await ctx_or_interaction.response.send_message(start_msg)
    else:
        await ctx_or_interaction.send(start_msg)

    success_count, failed_count = 0, 0
    for member in guild.members:
        if member.bot: continue
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

    end_msg = f"✅ Sent ({format_type}): {success_count} | ❌ Failed: {failed_count}"
    if is_interaction:
        await ctx_or_interaction.followup.send(end_msg)
    else:
        await ctx_or_interaction.send(end_msg)


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

        embed = discord.Embed(title="Welcome!", description=description_text, color=discord.Color.blue())
        embed.set_thumbnail(url=member.display_avatar.url)
        if custom_welcome_img:
            embed.set_image(url=custom_welcome_img)
        embed.set_footer(text=f"Member #{guild.member_count}")
        await target_channel.send(content=f"Welcome {member.mention}!", embed=embed)


# ==============================================================================
# HELP COMMANDS MODULE
# ==============================================================================

@bot.tree.command(name="antinuke_help", description="Show all Anti-Nuke configuration commands.")
@bot.command(name="antinuke_help")
async def help_antinuke(ctx_or_interaction):
    embed = discord.Embed(title="🛡️ Anti-Nuke & Anti-Spam Commands", color=discord.Color.red())
    embed.add_field(name="/set_ban_limit <limit>", value="Set max ban limit threshold", inline=False)
    embed.add_field(name="/set_channel_limit <limit>", value="Set max channel deletion limit", inline=False)
    embed.add_field(name="/set_spam_limit <msgs>", value="Set message spam speed threshold", inline=False)
    
    if isinstance(ctx_or_interaction, discord.Interaction):
        await ctx_or_interaction.response.send_message(embed=embed)
    else:
        await ctx_or_interaction.send(embed=embed)


@bot.tree.command(name="ticket_help", description="Show all Ticket Panel management commands.")
@bot.command(name="ticket_help")
async def help_ticket(ctx_or_interaction):
    embed = discord.Embed(title="🎫 Ticket System Commands", color=discord.Color.blue())
    embed.add_field(name="/setup_ticket", value="Send ticket panel interface with dropdown", inline=False)
    embed.add_field(name="/ticket_log_channel <channel>", value="Set channel for transcripts", inline=False)
    embed.add_field(name="/set_ticket_ping <msg>", value="Set custom mention message", inline=False)
    
    if isinstance(ctx_or_interaction, discord.Interaction):
        await ctx_or_interaction.response.send_message(embed=embed)
    else:
        await ctx_or_interaction.send(embed=embed)


@bot.tree.command(name="giveaway_help", description="Show all Giveaway system commands.")
@bot.command(name="giveaway_help")
async def help_giveaway(ctx_or_interaction):
    embed = discord.Embed(title="🎉 Giveaway System Commands", color=discord.Color.gold())
    embed.add_field(name="/giveaway <prize> <duration> [winners] [fixed_winner]", value="Start giveaway with optional fixed winner", inline=False)
    
    if isinstance(ctx_or_interaction, discord.Interaction):
        await ctx_or_interaction.response.send_message(embed=embed)
    else:
        await ctx_or_interaction.send(embed=embed)


@bot.tree.command(name="welcome_help", description="Show all Welcome System commands.")
@bot.command(name="welcome_help")
async def help_welcome(ctx_or_interaction):
    embed = discord.Embed(title="👋 Welcome System Commands", color=discord.Color.green())
    embed.add_field(name="/setup_welcome", value="Set welcome channel, message, and banner via interactive modal", inline=False)
    embed.add_field(name="/disable_welcome", value="Turn off welcome system", inline=False)
    
    if isinstance(ctx_or_interaction, discord.Interaction):
        await ctx_or_interaction.response.send_message(embed=embed)
    else:
        await ctx_or_interaction.send(embed=embed)


@bot.tree.command(name="invites_help", description="Show all Invite Tracker commands.")
@bot.command(name="invites_help")
async def help_invites(ctx_or_interaction):
    embed = discord.Embed(title="📊 Invite Tracker Commands", color=discord.Color.gold())
    embed.add_field(name="/setup_invitelog [channel]", value="Set invite logging channel", inline=False)
    embed.add_field(name="/invites [member]", value="Check member invite count", inline=False)
    
    if isinstance(ctx_or_interaction, discord.Interaction):
        await ctx_or_interaction.response.send_message(embed=embed)
    else:
        await ctx_or_interaction.send(embed=embed)


@bot.tree.command(name="moderation_help", description="Show all Moderation commands.")
@bot.command(name="moderation_help")
async def help_mod(ctx_or_interaction):
    embed = discord.Embed(title="🔨 Moderation Commands", color=discord.Color.purple())
    embed.add_field(name="/ban <member> [reason]", value="Permanently ban a member", inline=False)
    embed.add_field(name="/mute <member> <time> [reason]", value="Timeout member (e.g. 10m, 1h)", inline=False)
    embed.add_field(name="/purge <amount>", value="Clear up to 100 messages", inline=False)
    embed.add_field(name="/role <add/remove> <member> <role>", value="Manage roles quickly", inline=False)
    embed.add_field(name="/set_prefix <prefix>", value="Change text prefix", inline=False)
    embed.add_field(name="/dmall <message>", value="Broadcast DMs to server members", inline=False)
    
    if isinstance(ctx_or_interaction, discord.Interaction):
        await ctx_or_interaction.response.send_message(embed=embed)
    else:
        await ctx_or_interaction.send(embed=embed)


# ==============================================================================
# KEEP ALIVE & BOT START EXECUTION
# ==============================================================================

keep_alive()

BOT_TOKEN = os.getenv("DISCORD_TOKEN")
if BOT_TOKEN:
    bot.run(BOT_TOKEN)
else:
    print("❌ Error: DISCORD_TOKEN environment variable not found!")
