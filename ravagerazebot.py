import os
import random
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Literal
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Modal, TextInput

# Placeholder for keep_alive if defined in your environment
try:
    from keep_alive import keep_alive
except ImportError:
    def keep_alive():
        pass

# Global States & Configurations
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

# Dynamic Ticket Options & Questions storage for editable features (Emojis Removed)
ticket_options = [
    {"label": "Support", "description": "Open a general support ticket", "questions": ["What is your issue?"]},
    {"label": "Report", "description": "Report a user or bug", "questions": ["Who/What are you reporting?", "Provide proof/details"]}
]

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.invites = True
intents.message_content = True

bot = commands.Bot(command_prefix=commands.when_mentioned_or(custom_prefix), intents=intents)

def is_whitelisted(user: discord.Member, guild: discord.Guild) -> bool:
    return user.guild_permissions.administrator

def parse_time(duration_str: str) -> int:
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    unit = duration_str[-1].lower()
    val = duration_str[:-1]
    if unit in multipliers and val.isdigit():
        return int(val) * multipliers[unit]
    return 60

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s).")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
     
    for guild in bot.guilds:
        try:
            invites_cache[guild.id] = await guild.invites()
        except Exception:
            pass

# ==================== HYBRID PREFIX / SLASH SUPPORT WRAPPER ====================

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    # Check if message starts with custom prefix
    if message.content.startswith(custom_prefix):
        ctx = await bot.get_context(message)
        if ctx.command:
            await bot.invoke(ctx)
            return

    await bot.process_commands(message)


# ==================== TICKET EDITABLE SYSTEM ====================

class TicketSetupModal(Modal, title="Edit Ticket Options & Questions"):
    option_label = TextInput(
        label="Option Label",
        placeholder="e.g. Support, Billing",
        default="Support",
        max_length=100
    )
    option_desc = TextInput(
        label="Option Description",
        placeholder="Brief description of this ticket type",
        default="Open a support ticket",
        max_length=200
    )
    option_questions = TextInput(
        label="Questions (Separated by comma ,)",
        style=discord.TextStyle.paragraph,
        placeholder="What is your issue?, Provide details",
        default="What is your issue?",
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        global ticket_options
        questions_list = [q.strip() for q in self.option_questions.value.split(",") if q.strip()]
        
        found = False
        for opt in ticket_options:
            if opt["label"].lower() == self.option_label.value.strip().lower():
                opt["description"] = self.option_desc.value.strip()
                opt["questions"] = questions_list
                found = True
                break
        
        if not found:
            ticket_options.append({
                "label": self.option_label.value.strip(),
                "description": self.option_desc.value.strip(),
                "questions": questions_list
            })

        await interaction.response.send_message(
            f"✅ **Ticket Option Updated/Added Successfully!**\n🏷️ **Label:** `{self.option_label.value}`\n📝 **Questions Count:** `{len(questions_list)}`",
            ephemeral=True
        )

class TicketDropdown(discord.ui.Select):
    def __init__(self):
        options = []
        for opt in ticket_options:
            options.append(discord.SelectOption(label=opt["label"], description=opt["description"]))
        super().__init__(placeholder="Select a ticket type to open...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_option = self.values[0]
        opt_data = next((opt for opt in ticket_options if opt["label"] == selected_option), None)
        
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }
        
        category = interaction.channel.category
        ticket_channel = await interaction.guild.create_text_channel(
            name=f"ticket-{interaction.user.name}",
            category=category,
            overwrites=overwrites
        )

        ping_msg = custom_ticket_ping.replace("{user}", interaction.user.mention).replace("{role}", "@here")
        embed = discord.Embed(
            title=f"Ticket: {selected_option}",
            description=f"Welcome {interaction.user.mention}!\nPlease answer the following questions:\n",
            color=discord.Color.blue()
        )
        
        if opt_data and opt_data["questions"]:
            for i, q in enumerate(opt_data["questions"], 1):
                embed.add_field(name=f"Question {i}", value=q, inline=False)

        await ticket_channel.send(content=ping_msg, embed=embed)
        await interaction.response.send_message(f"✅ Ticket created: {ticket_channel.mention}", ephemeral=True)

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketDropdown())

@bot.tree.command(name="setup_ticket", description="Launch interactive setup wizard for clean ticket panel UI (No Emojis)")
@bot.command(name="setup_ticket")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setup_ticket(interaction_or_ctx, role: discord.Role = None, category: discord.CategoryChannel = None):
    is_interaction = isinstance(interaction_or_ctx, discord.Interaction)
    author = interaction_or_ctx.user if is_interaction else interaction_or_ctx.author
    guild = interaction_or_ctx.guild

    if not is_whitelisted(author, guild) and not author.guild_permissions.administrator:
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction:
            await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        else:
            await interaction_or_ctx.send(msg)
        return

    embed = discord.Embed(
        title="Support Ticket Panel",
        description="Select an option from the dropdown menu below to open a ticket.",
        color=discord.Color.blue()
    )
    view = TicketView()
    
    if is_interaction:
        await interaction_or_ctx.response.send_message(embed=embed, view=view)
    else:
        await interaction_or_ctx.send(embed=embed, view=view)

@bot.tree.command(name="edit_ticket_options", description="Directly edit ticket options and questions without rebuilding setup.")
@bot.command(name="edit_ticket_options")
@app_commands.checks.has_permissions(administrator=True)
async def slash_edit_ticket_options(interaction_or_ctx):
    is_interaction = isinstance(interaction_or_ctx, discord.Interaction)
    if is_interaction:
        await interaction_or_ctx.response.send_modal(TicketSetupModal())
    else:
        await interaction_or_ctx.send("⚠️ Please use the slash command `/edit_ticket_options` to open the interactive edit modal.")


# ==================== GIVEAWAY LOGIC ====================

@bot.tree.command(name="giveaway", description="Start a giveaway with optional fixed/custom winner.")
@bot.command(name="giveaway")
@app_commands.checks.has_permissions(manage_guild=True)
async def slash_giveaway(interaction_or_ctx, prize: str, duration: str, winners_count: int = 1, fixed_winner: discord.Member = None):
    is_interaction = isinstance(interaction_or_ctx, discord.Interaction)
    channel = interaction_or_ctx.channel if is_interaction else interaction_or_ctx.message.channel
    user = interaction_or_ctx.user if is_interaction else interaction_or_ctx.author

    seconds = parse_time(duration)
    
    embed = discord.Embed(
        title="GIVEAWAY",
        description=f"**Prize:** {prize}\n**Winner(s):** `{winners_count}`\n**Hosted by:** {user.mention}\n\nReact with 🎉 to enter!",
        color=discord.Color.gold()
    )
    embed.set_footer(text=f"Ends in {duration}")
    
    if is_interaction:
        await interaction_or_ctx.response.send_message(embed=embed)
        msg = await interaction_or_ctx.original_response()
    else:
        msg = await interaction_or_ctx.send(embed=embed)

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

    guild = interaction_or_ctx.guild
    while len(chosen_winners) < winners_count and participants:
        winner_id = random.choice(participants)
        participants.remove(winner_id)
        member = guild.get_member(winner_id)
        if member:
            chosen_winners.append(member)

    if chosen_winners:
        winners_mention = ", ".join([w.mention for w in chosen_winners])
        result_embed = discord.Embed(
            title="GIVEAWAY ENDED",
            description=f"**Prize:** {prize}\n**Winner(s):** {winners_mention} 🏆",
            color=discord.Color.green()
        )
        await msg.edit(embed=result_embed, view=None)
        await channel.send(f"Congratulations {winners_mention}! You won **{prize}**!")
    else:
        result_embed = discord.Embed(
            title="GIVEAWAY ENDED",
            description=f"**Prize:** {prize}\n❌ No valid participants found.",
            color=discord.Color.red()
        )
        await msg.edit(embed=result_embed, view=None)


# ==================== CONFIGURATION & ADMIN COMMANDS ====================

@bot.tree.command(name="set_ban_limit", description="Set anti-nuke max ban threshold limit.")
@bot.command(name="set_ban_limit")
@app_commands.checks.has_permissions(administrator=True)
async def slash_set_ban_limit(interaction_or_ctx, limit: int):
    is_interaction = isinstance(interaction_or_ctx, discord.Interaction)
    user = interaction_or_ctx.user if is_interaction else interaction_or_ctx.author
    guild = interaction_or_ctx.guild

    if not is_whitelisted(user, guild) and not user.guild_permissions.administrator:
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        else: await interaction_or_ctx.send(msg)
        return

    global ban_limit
    ban_limit = limit
    res = f"✅ **Anti-Nuke Ban Limit updated to:** `{ban_limit}` bans / 2 mins"
    if is_interaction: await interaction_or_ctx.response.send_message(res)
    else: await interaction_or_ctx.send(res)


@bot.tree.command(name="set_channel_limit", description="Set anti-nuke max channel delete threshold limit.")
@bot.command(name="set_channel_limit")
@app_commands.checks.has_permissions(administrator=True)
async def slash_set_channel_limit(interaction_or_ctx, limit: int):
    is_interaction = isinstance(interaction_or_ctx, discord.Interaction)
    user = interaction_or_ctx.user if is_interaction else interaction_or_ctx.author
    guild = interaction_or_ctx.guild

    if not is_whitelisted(user, guild) and not user.guild_permissions.administrator:
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        else: await interaction_or_ctx.send(msg)
        return

    global channel_limit
    channel_limit = limit
    res = f"✅ **Anti-Nuke Channel Delete Limit updated to:** `{channel_limit}` channels / 2 mins"
    if is_interaction: await interaction_or_ctx.response.send_message(res)
    else: await interaction_or_ctx.send(res)


@bot.tree.command(name="set_spam_limit", description="Set max allowed messages within 5 seconds before mute.")
@bot.command(name="set_spam_limit")
@app_commands.checks.has_permissions(administrator=True)
async def slash_set_spam_limit(interaction_or_ctx, messages_count: int):
    is_interaction = isinstance(interaction_or_ctx, discord.Interaction)
    user = interaction_or_ctx.user if is_interaction else interaction_or_ctx.author
    guild = interaction_or_ctx.guild

    if not is_whitelisted(user, guild) and not user.guild_permissions.administrator:
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        else: await interaction_or_ctx.send(msg)
        return

    global spam_limit
    spam_limit = messages_count
    res = f"✅ **Anti-Spam Limit updated to:** `{spam_limit}` msgs / 5 sec"
    if is_interaction: await interaction_or_ctx.response.send_message(res)
    else: await interaction_or_ctx.send(res)


@bot.tree.command(name="set_prefix", description="Set custom prefix for text commands.")
@bot.command(name="set_prefix")
@app_commands.checks.has_permissions(administrator=True)
async def slash_set_prefix(interaction_or_ctx, prefix: str):
    is_interaction = isinstance(interaction_or_ctx, discord.Interaction)
    user = interaction_or_ctx.user if is_interaction else interaction_or_ctx.author
    guild = interaction_or_ctx.guild

    if not is_whitelisted(user, guild) and not user.guild_permissions.administrator:
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        else: await interaction_or_ctx.send(msg)
        return

    global custom_prefix
    custom_prefix = prefix
    bot.command_prefix = commands.when_mentioned_or(custom_prefix)
    res = f"✅ **Custom Prefix updated to:** `{custom_prefix}`"
    if is_interaction: await interaction_or_ctx.response.send_message(res)
    else: await interaction_or_ctx.send(res)


@bot.tree.command(name="role", description="Assign or remove a role from a member easily.")
@bot.command(name="role")
@app_commands.checks.has_permissions(manage_roles=True)
async def slash_role(interaction_or_ctx, action: Literal["add", "remove"], member: discord.Member, role: discord.Role):
    is_interaction = isinstance(interaction_or_ctx, discord.Interaction)
    user = interaction_or_ctx.user if is_interaction else interaction_or_ctx.author
    guild = interaction_or_ctx.guild

    if not is_whitelisted(user, guild) and not user.guild_permissions.manage_roles:
        msg = "❌ **Access Denied:** You lack role management permissions."
        if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        else: await interaction_or_ctx.send(msg)
        return

    if action == "add":
        await member.add_roles(role, reason=f"Managed by {user}")
        res = f"✅ Successfully added **{role.name}** to {member.mention}!"
    else:
        await member.remove_roles(role, reason=f"Managed by {user}")
        res = f"✅ Successfully removed **{role.name}** from {member.mention}!"

    if is_interaction: await interaction_or_ctx.response.send_message(res)
    else: await interaction_or_ctx.send(res)


@bot.tree.command(name="ticket_log_channel", description="Set log channel for closed ticket transcripts.")
@bot.command(name="ticket_log_channel")
@app_commands.checks.has_permissions(administrator=True)
async def slash_ticket_log_channel(interaction_or_ctx, channel: discord.TextChannel):
    is_interaction = isinstance(interaction_or_ctx, discord.Interaction)
    user = interaction_or_ctx.user if is_interaction else interaction_or_ctx.author
    guild = interaction_or_ctx.guild

    if not is_whitelisted(user, guild) and not user.guild_permissions.administrator:
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        else: await interaction_or_ctx.send(msg)
        return

    global ticket_log_channel_id
    ticket_log_channel_id = channel.id
    res = f"✅ **Ticket Transcript Log Channel set to:** {channel.mention}"
    if is_interaction: await interaction_or_ctx.response.send_message(res)
    else: await interaction_or_ctx.send(res)


@bot.tree.command(name="set_ticket_ping", description="Customize ticket opening ping message.")
@bot.command(name="set_ticket_ping")
@app_commands.checks.has_permissions(administrator=True)
async def slash_set_ticket_ping(interaction_or_ctx, message: str):
    is_interaction = isinstance(interaction_or_ctx, discord.Interaction)
    user = interaction_or_ctx.user if is_interaction else interaction_or_ctx.author
    guild = interaction_or_ctx.guild

    if not is_whitelisted(user, guild) and not user.guild_permissions.administrator:
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        else: await interaction_or_ctx.send(msg)
        return

    global custom_ticket_ping
    custom_ticket_ping = message
    res = f"✅ **Ticket Open Ping updated!**\nFormat: `{message}`"
    if is_interaction: await interaction_or_ctx.response.send_message(res)
    else: await interaction_or_ctx.send(res)


@bot.tree.command(name="setup_invitelog", description="Set channel for invite logs.")
@bot.command(name="setup_invitelog")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setup_invitelog(interaction_or_ctx, channel: discord.TextChannel = None):
    is_interaction = isinstance(interaction_or_ctx, discord.Interaction)
    user = interaction_or_ctx.user if is_interaction else interaction_or_ctx.author
    guild = interaction_or_ctx.guild

    if not is_whitelisted(user, guild) and not user.guild_permissions.administrator:
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        else: await interaction_or_ctx.send(msg)
        return

    global invite_log_channel_id
    target = channel or (interaction_or_ctx.channel if is_interaction else interaction_or_ctx.message.channel)
    invite_log_channel_id = target.id
    res = f"✅ **Invite Logger set to:** {target.mention}"
    if is_interaction: await interaction_or_ctx.response.send_message(res)
    else: await interaction_or_ctx.send(res)


@bot.tree.command(name="invites", description="Check invite stats of a server member.")
@bot.command(name="invites")
async def slash_invites(interaction_or_ctx, member: discord.Member = None):
    is_interaction = isinstance(interaction_or_ctx, discord.Interaction)
    target = member or (interaction_or_ctx.user if is_interaction else interaction_or_ctx.author)
    guild = interaction_or_ctx.guild

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
        await interaction_or_ctx.response.send_message(embed=embed)
    else:
        await interaction_or_ctx.send(embed=embed)


@bot.tree.command(name="dmall", description="Send DM announcement to all server members.")
@app_commands.describe(message="The message you want to broadcast", as_embed="True for Embed format, False for Plain Text")
@bot.command(name="dmall")
@app_commands.checks.has_permissions(administrator=True)
async def slash_dmall(interaction_or_ctx, message: str, as_embed: bool = False):
    is_interaction = isinstance(interaction_or_ctx, discord.Interaction)
    user = interaction_or_ctx.user if is_interaction else interaction_or_ctx.author
    guild = interaction_or_ctx.guild

    if not is_whitelisted(user, guild) and not user.guild_permissions.administrator:
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        else: await interaction_or_ctx.send(msg)
        return
        
    format_type = "Embed" if as_embed else "Plain Text"
    start_msg = f"⏳ **Starting DM Broadcast ({format_type})...** Safe delay active."
    if is_interaction:
        await interaction_or_ctx.response.send_message(start_msg)
    else:
        await interaction_or_ctx.send(start_msg)

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
        await interaction_or_ctx.followup.send(end_msg)
    else:
        await interaction_or_ctx.send(end_msg)


# ==================== WELCOME SYSTEM & MODALS ====================

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
async def slash_setup_welcome(interaction_or_ctx):
    is_interaction = isinstance(interaction_or_ctx, discord.Interaction)
    user = interaction_or_ctx.user if is_interaction else interaction_or_ctx.author
    guild = interaction_or_ctx.guild

    if not is_whitelisted(user, guild) and not user.guild_permissions.administrator:
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        else: await interaction_or_ctx.send(msg)
        return
    
    view = WelcomeSelectView()
    msg = "📌 **Please select your welcome channel from the dropdown below:**"
    if is_interaction:
        await interaction_or_ctx.response.send_message(msg, view=view, ephemeral=True)
    else:
        await interaction_or_ctx.send(msg, view=view)


@bot.tree.command(name="disable_welcome", description="Turn off welcome system.")
@bot.command(name="disable_welcome")
@app_commands.checks.has_permissions(administrator=True)
async def slash_disable_welcome(interaction_or_ctx):
    is_interaction = isinstance(interaction_or_ctx, discord.Interaction)
    user = interaction_or_ctx.user if is_interaction else interaction_or_ctx.author
    guild = interaction_or_ctx.guild

    if not is_whitelisted(user, guild) and not user.guild_permissions.administrator:
        msg = "❌ **Access Denied:** You need administrator permissions."
        if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        else: await interaction_or_ctx.send(msg)
        return

    global welcome_enabled
    welcome_enabled = False
    res = "❌ **Welcome system has been disabled.**"
    if is_interaction: await interaction_or_ctx.response.send_message(res)
    else: await interaction_or_ctx.send(res)


@bot.tree.command(name="ban", description="Ban a member.")
@bot.command(name="ban")
@app_commands.checks.has_permissions(ban_members=True)
async def slash_ban(interaction_or_ctx, member: discord.Member, reason: str = "No reason provided"):
    is_interaction = isinstance(interaction_or_ctx, discord.Interaction)
    user = interaction_or_ctx.user if is_interaction else interaction_or_ctx.author
    guild = interaction_or_ctx.guild

    if not is_whitelisted(user, guild) and not user.guild_permissions.ban_members:
        msg = "❌ **Access Denied:** You lack ban permissions."
        if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        else: await interaction_or_ctx.send(msg)
        return
    if is_whitelisted(member, guild):
        msg = "❌ **Access Denied:** User is whitelisted."
        if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        else: await interaction_or_ctx.send(msg)
        return

    await member.ban(reason=reason)
    res = f"🔨 **{member.mention} banned!**"
    if is_interaction: await interaction_or_ctx.response.send_message(res)
    else: await interaction_or_ctx.send(res)


@bot.tree.command(name="mute", description="Timeout a member.")
@bot.command(name="mute")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_mute(interaction_or_ctx, member: discord.Member, duration: str, reason: str = "No reason provided"):
    is_interaction = isinstance(interaction_or_ctx, discord.Interaction)
    user = interaction_or_ctx.user if is_interaction else interaction_or_ctx.author
    guild = interaction_or_ctx.guild

    if not is_whitelisted(user, guild) and not user.guild_permissions.moderate_members:
        msg = "❌ **Access Denied:** You lack timeout permissions."
        if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        else: await interaction_or_ctx.send(msg)
        return
    if is_whitelisted(member, guild):
        msg = "❌ **Access Denied:** User is whitelisted."
        if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        else: await interaction_or_ctx.send(msg)
        return

    seconds = parse_time(duration)
    await member.timeout(timedelta(seconds=seconds), reason=reason)
    res = f"🤐 **{member.mention} timed out for {duration}!**"
    if is_interaction: await interaction_or_ctx.response.send_message(res)
    else: await interaction_or_ctx.send(res)


@bot.tree.command(name="purge", description="Bulk delete messages in current channel.")
@bot.command(name="purge")
@app_commands.checks.has_permissions(manage_messages=True)
async def slash_purge(interaction_or_ctx, amount: int):
    is_interaction = isinstance(interaction_or_ctx, discord.Interaction)
    user = interaction_or_ctx.user if is_interaction else interaction_or_ctx.author
    guild = interaction_or_ctx.guild
    channel = interaction_or_ctx.channel if is_interaction else interaction_or_ctx.message.channel

    if not is_whitelisted(user, guild) and not user.guild_permissions.manage_messages:
        msg = "❌ **Access Denied:** You lack message management permissions."
        if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        else: await interaction_or_ctx.send(msg)
        return
    if amount < 1 or amount > 100:
        msg = "❌ Please specify an amount between 1 and 100."
        if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        else: await interaction_or_ctx.send(msg)
        return

    if is_interaction:
        await interaction_or_ctx.response.defer(ephemeral=True)
        deleted = await channel.purge(limit=amount)
        await interaction_or_ctx.followup.send(f"🧹 Successfully deleted **{len(deleted)}** messages!", ephemeral=True)
    else:
        deleted = await channel.purge(limit=amount)
        await interaction_or_ctx.send(f"🧹 Successfully deleted **{len(deleted)}** messages!", delete_after=5)


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

        embed = discord.Embed(title="Welcome!", description=description_text, color=discord.Color.blue())
        embed.set_thumbnail(url=member.display_avatar.url)
        if custom_welcome_img:
            embed.set_image(url=custom_welcome_img)
        embed.set_footer(text=f"Member #{guild.member_count}")
        await target_channel.send(content=f"Welcome {member.mention}!", embed=embed)


# ==================== HELP COMMANDS ====================

@bot.tree.command(name="antinuke_help", description="Show all Anti-Nuke and Anti-Spam configuration commands.")
@bot.command(name="antinuke_help")
async def help_antinuke(interaction_or_ctx):
    embed = discord.Embed(title="🛡️ Anti-Nuke & Anti-Spam Commands", color=discord.Color.red())
    embed.add_field(name="/set_ban_limit <limit>", value="Set max ban limit threshold", inline=False)
    embed.add_field(name="/set_channel_limit <limit>", value="Set max channel deletion limit", inline=False)
    embed.add_field(name="/set_spam_limit <msgs>", value="Set message spam speed threshold", inline=False)
    
    if isinstance(interaction_or_ctx, discord.Interaction):
        await interaction_or_ctx.response.send_message(embed=embed)
    else:
        await interaction_or_ctx.send(embed=embed)


@bot.tree.command(name="ticket_help", description="Show all Ticket Panel management commands.")
@bot.command(name="ticket_help")
async def help_ticket(interaction_or_ctx):
    embed = discord.Embed(title="🎫 Ticket System Commands", color=discord.Color.blue())
    embed.add_field(name="/setup_ticket", value="Launch interactive setup wizard for clean ticket panel UI", inline=False)
    embed.add_field(name="/edit_ticket_options", value="Directly edit ticket options and questions dynamically", inline=False)
    embed.add_field(name="/ticket_log_channel <channel>", value="Set channel for closed ticket text transcripts", inline=False)
    embed.add_field(name="/set_ticket_ping <msg>", value="Set custom ticket mention message", inline=False)
    
    if isinstance(interaction_or_ctx, discord.Interaction):
        await interaction_or_ctx.response.send_message(embed=embed)
    else:
        await interaction_or_ctx.send(embed=embed)


@bot.tree.command(name="giveaway_help", description="Show all Giveaway system commands.")
@bot.command(name="giveaway_help")
async def help_giveaway(interaction_or_ctx):
    embed = discord.Embed(title="🎉 Giveaway System Commands", color=discord.Color.gold())
    embed.add_field(name="/giveaway <prize> <duration> [winners] [fixed_winner]", value="Start giveaway with optional fixed/custom winner", inline=False)
    
    if isinstance(interaction_or_ctx, discord.Interaction):
        await interaction_or_ctx.response.send_message(embed=embed)
    else:
        await interaction_or_ctx.send(embed=embed)


@bot.tree.command(name="welcome_help", description="Show all Welcome System commands.")
@bot.command(name="welcome_help")
async def help_welcome(interaction_or_ctx):
    embed = discord.Embed(title="👋 Welcome System Commands", color=discord.Color.green())
    embed.add_field(name="/setup_welcome", value="Set welcome channel, message, and banner image via modal wizard", inline=False)
    embed.add_field(name="/disable_welcome", value="Turn off welcome system", inline=False)
    
    if isinstance(interaction_or_ctx, discord.Interaction):
        await interaction_or_ctx.response.send_message(embed=embed)
    else:
        await interaction_or_ctx.send(embed=embed)


@bot.tree.command(name="invites_help", description="Show all Invite Tracker commands.")
@bot.command(name="invites_help")
async def help_invites(interaction_or_ctx):
    embed = discord.Embed(title="📊 Invite Tracker Commands", color=discord.Color.gold())
    embed.add_field(name="/setup_invitelog [channel]", value="Set invite logging channel", inline=False)
    embed.add_field(name="/invites [member]", value="Check member invite count", inline=False)
    
    if isinstance(interaction_or_ctx, discord.Interaction):
        await interaction_or_ctx.response.send_message(embed=embed)
    else:
        await interaction_or_ctx.send(embed=embed)


@bot.tree.command(name="moderation_help", description="Show all Moderation commands.")
@bot.command(name="moderation_help")
async def help_mod(interaction_or_ctx):
    embed = discord.Embed(title="🔨 Moderation Commands", color=discord.Color.purple())
    embed.add_field(name="/ban <member> [reason]", value="Permanently ban a member", inline=False)
    embed.add_field(name="/mute <member> <time> [reason]", value="Timeout member (e.g. 10m, 1h)", inline=False)
    embed.add_field(name="/purge <amount>", value="Clear up to 100 messages in channel", inline=False)
    embed.add_field(name="/role <add/remove> <member> <role>", value="Quickly assign or remove roles", inline=False)
    embed.add_field(name="/set_prefix <prefix>", value="Change bot command prefix", inline=False)
    embed.add_field(name="/dmall <message>", value="Broadcast announcement via DMs", inline=False)
    
    if isinstance(interaction_or_ctx, discord.Interaction):
        await interaction_or_ctx.response.send_message(embed=embed)
    else:
        await interaction_or_ctx.send(embed=embed)


# ==================== RUN BOT ====================

keep_alive()
BOT_TOKEN = os.getenv("DISCORD_TOKEN")
if BOT_TOKEN:
    bot.run(BOT_TOKEN)
else:
    print("❌ Error: DISCORD_TOKEN environment variable not found!")

