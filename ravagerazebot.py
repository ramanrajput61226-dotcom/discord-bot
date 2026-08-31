import discord
from discord.ext import commands
from discord.app_commands import Choice
from discord import app_commands
from discord.ui import Modal, TextInput, Select, View
from datetime import datetime, timezone, timedelta
from typing import Literal
import asyncio
import os
import io

# ==============================================================================
# BOT SETUP & INITIALIZATION
# ==============================================================================

intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True
intents.invites = True

class HybridBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        print("🌍 Slash commands synced successfully.")

bot = HybridBot()

# Global Storage / Configuration Variables
prefix = "!"
ban_limit_threshold = 3
channel_limit_threshold = 3
spam_limit_threshold = 5

ticket_log_channel_id = None
custom_ticket_ping = "Support will be with you shortly."

welcome_enabled = False
welcome_channel_id = None
custom_welcome_msg = "Hey {user}, welcome to **{server}**! Member count: {count}"
custom_welcome_img = None
invite_log_channel_id = None
invites_cache = {}

# ==============================================================================
# HELPER FUNCTIONS & ANTI-NUKE UTILS
# ==============================================================================

def is_whitelisted(user: discord.Member, guild: discord.Guild) -> bool:
    if user.id == guild.owner_id or user.guild_permissions.administrator:
        return True
    return False

def parse_time(time_str: str) -> int:
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    unit = time_str[-1].lower()
    val = int(time_str[:-1])
    return val * multipliers.get(unit, 1)

@bot.event
async def on_ready():
    print(f"🤖 Logged in as {bot.user} (ID: {bot.user.id})")
    for guild in bot.guilds:
        try:
            invites_cache[guild.id] = await guild.invites()
        except Exception:
            pass

# ==============================================================================
# ANTI-NUKE & CONFIGURATION COMMANDS
# ==============================================================================

@bot.tree.command(name="set_ban_limit", description="Set max ban limit threshold for anti-nuke.")
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

    global ban_limit_threshold
    ban_limit_threshold = limit
    res = f"✅ **Anti-Nuke Ban Limit updated to:** `{limit}`"
    if is_interaction: await ctx_or_interaction.response.send_message(res)
    else: await ctx_or_interaction.send(res)


@bot.tree.command(name="set_channel_limit", description="Set max channel deletion limit.")
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

    global channel_limit_threshold
    channel_limit_threshold = limit
    res = f"✅ **Anti-Nuke Channel Limit updated to:** `{limit}`"
    if is_interaction: await ctx_or_interaction.response.send_message(res)
    else: await ctx_or_interaction.send(res)


@bot.tree.command(name="set_spam_limit", description="Set message spam speed threshold.")
@bot.command(name="set_spam_limit")
@app_commands.checks.has_permissions(administrator=True)
async def set_spam_limit(ctx_or_interaction, limit: int):
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    user = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
    guild = ctx_or_interaction.guild

    if not is_whitelisted(user, guild):
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else: await ctx_or_interaction.send(msg)
        return

    global spam_limit_threshold
    spam_limit_threshold = limit
    res = f"✅ **Anti-Spam Limit updated to:** `{limit}` messages"
    if is_interaction: await ctx_or_interaction.response.send_message(res)
    else: await ctx_or_interaction.send(res)


@bot.tree.command(name="set_prefix", description="Change text command prefix.")
@bot.command(name="set_prefix")
@app_commands.checks.has_permissions(administrator=True)
async def set_prefix(ctx_or_interaction, new_prefix: str):
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    user = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
    guild = ctx_or_interaction.guild

    if not is_whitelisted(user, guild):
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else: await ctx_or_interaction.send(msg)
        return

    global prefix
    prefix = new_prefix
    bot.command_prefix = new_prefix
    res = f"✅ **Prefix updated successfully to:** `{new_prefix}`"
    if is_interaction: await ctx_or_interaction.response.send_message(res)
    else: await ctx_or_interaction.send(res)


# ==============================================================================
# TICKET SYSTEM MODULE (MODALS, VIEWS & COMMANDS)
# ==============================================================================

class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket_btn")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔒 **Closing ticket and generating transcript...**", ephemeral=True)
        
        # Transcript generation logic
        messages = [f"{msg.author}: {msg.content}" async for msg in interaction.channel.history(limit=200, oldest_first=True)]
        transcript_content = "\n".join(messages)
        file = discord.File(io.BytesIO(transcript_content.encode('utf-8')), filename=f"transcript-{interaction.channel.name}.txt")

        if ticket_log_channel_id:
            log_channel = interaction.guild.get_channel(ticket_log_channel_id)
            if log_channel:
                await log_channel.send(f"📁 **Transcript for closed ticket:** `{interaction.channel.name}`", file=file)

        await asyncio.sleep(3)
        await interaction.channel.delete()


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="Select ticket category...",
        custom_id="ticket_dropdown_select",
        options=[
            discord.SelectOption(label="Support / General", description="Open general support ticket", emoji="🎫"),
            discord.SelectOption(label="Purchase / Store", description="Inquire about store ranks or items", emoji="🛒"),
            discord.SelectOption(label="Report Player", description="Report a rule breaker", emoji="⚠️")
        ]
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        guild = interaction.guild
        category_name = select.values[0]
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }
        
        ticket_channel = await guild.create_text_channel(
            name=f"ticket-{interaction.user.name}",
            overwrites=overwrites
        )

        embed = discord.Embed(
            title=f"Ticket: {category_name}",
            description=f"Welcome {interaction.user.mention}!\n{custom_ticket_ping}",
            color=discord.Color.blue()
        )
        await ticket_channel.send(content=interaction.user.mention, embed=embed, view=TicketControlView())
        await interaction.response.send_message(f"✅ Your ticket has been created: {ticket_channel.mention}", ephemeral=True)


@bot.tree.command(name="ticket_log_channel", description="Set channel for saving ticket transcripts.")
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
        title="Support Ticket Panel",
        description="Select an option from the dropdown menu below to open a ticket.",
        color=discord.Color.blue()
    )
    view = TicketView()
    if is_interaction:
        await ctx_or_interaction.response.send_message(embed=embed, view=view)
    else:
        await ctx_or_interaction.send(embed=embed, view=view)


# ==============================================================================
# GIVEAWAY SYSTEM MODULE
# ==============================================================================

class GiveawayView(discord.ui.View):
    def __init__(self, fixed_winner: discord.Member = None):
        super().__init__(timeout=None)
        self.participants = set()
        self.fixed_winner = fixed_winner

    @discord.ui.button(label="🎉 Enter Giveaway", style=discord.ButtonStyle.green, custom_id="enter_giveaway_btn")
    async def enter_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.participants:
            await interaction.response.send_message("❌ You have already entered this giveaway!", ephemeral=True)
        else:
            self.participants.add(interaction.user.id)
            await interaction.response.send_message("✅ Entry recorded successfully!", ephemeral=True)


@bot.tree.command(name="giveaway", description="Start an interactive giveaway.")
@bot.command(name="giveaway")
@app_commands.checks.has_permissions(administrator=True)
async def giveaway(ctx_or_interaction, prize: str, duration: str, winners: int = 1, fixed_winner: discord.Member = None):
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    user = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
    guild = ctx_or_interaction.guild

    if not is_whitelisted(user, guild):
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else: await ctx_or_interaction.send(msg)
        return

    seconds = parse_time(duration)
    embed = discord.Embed(
        title="🎉 GIVEAWAY 🎉",
        description=f"**Prize:** {prize}\n**Winners:** {winners}\n**Duration:** {duration}\n\nClick the button below to join!",
        color=discord.Color.gold()
    )
    view = GiveawayView(fixed_winner=fixed_winner)

    if is_interaction:
        await ctx_or_interaction.response.send_message(embed=embed, view=view)
        msg = await ctx_or_interaction.original_response()
    else:
        msg = await ctx_or_interaction.send(embed=embed, view=view)

    await asyncio.sleep(seconds)

    # Pick winner logic
    import random
    participant_list = list(view.participants)
    chosen_winners = []

    if fixed_winner and fixed_winner.id in view.participants:
        chosen_winners.append(fixed_winner)
        winners -= 1

    if participant_list and winners > 0:
        remaining_count = min(winners, len(participant_list))
        random_ids = random.sample(participant_list, remaining_count)
        for uid in random_ids:
            m = guild.get_member(uid)
            if m and m not in chosen_winners:
                chosen_winners.append(m)

    winner_str = ", ".join([w.mention for w in chosen_winners]) if chosen_winners else "No valid entries"
    end_embed = discord.Embed(
        title="🎉 GIVEAWAY ENDED 🎉",
        description=f"**Prize:** {prize}\n**Winner(s):** {winner_str}",
        color=discord.Color.dark_gold()
    )
    await msg.edit(embed=end_embed, view=None)
    if chosen_winners:
        await msg.channel.send(f"Congratulations {winner_str}! You won **{prize}**!")


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
# BOT START EXECUTION
# ==============================================================================

BOT_TOKEN = os.getenv("DISCORD_TOKEN")
if BOT_TOKEN:
    bot.run(BOT_TOKEN)
else:
    print("❌ Error: DISCORD_TOKEN environment variable not found!")
 
