import discord
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput
from discord import app_commands
import asyncio
import random
from datetime import datetime, timedelta, timezone
from keep_alive import keep_alive
import os
import io
from typing import Literal

# ==================== INTENTS SETUP ====================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.moderation = True
intents.invites = True

# Custom Prefix Support Function
async def get_prefix(bot, message):
    return commands.when_mentioned_or(custom_prefix)(bot, message)

bot = commands.Bot(command_prefix=get_prefix, intents=intents)

# ==================== GLOBAL CONFIGURATION & TRACKERS ====================

custom_prefix = "!"
ban_limit = 5
channel_limit = 4
spam_limit = 5
spam_time_window = 5

ban_tracker = {}
channel_tracker = {}
banned_members_history = []
invites_cache = {}
message_tracker = {}
active_giveaways = {}

ticket_support_role_id = None
ticket_category_id = None
ticket_log_channel_id = None
custom_ticket_ping = "🔔 **New Ticket!** {role} - {user} needs assistance."
custom_ticket_title = "Help & Support"
custom_ticket_desc = (
    "Hi! Welcome to the Support 🎉\n"
    "Please select the option you want support for.\n"
    "Thanks For Supporting us 🥰"
)

# Active ticket panels stored for direct post-creation editing
active_ticket_panels = {} 

welcome_channel_id = None
welcome_enabled = True
custom_welcome_msg = None
custom_welcome_img = None
invite_log_channel_id = None

setup_wizards = {}
welcome_wizards = {}

# ==================== HELPER FUNCTIONS ====================

def clean_tracker(tracker, user_id, time_limit_seconds=120):
    now = datetime.now(timezone.utc)
    if user_id not in tracker:
        tracker[user_id] = []
    tracker[user_id] = [t for t in tracker[user_id] if (now - t).total_seconds() <= time_limit_seconds]
    return tracker[user_id]


def is_whitelisted(executor, guild):
    # Global Whitelist Bypass for your specific ID and username
    if executor.id == 1255544682759323680 or str(executor.name).lower() == "agnivanshii":
        return True
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


# ==================== TICKET SYSTEM CLASSES ====================

class TicketControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim Ticket 🖐️", style=discord.ButtonStyle.success, custom_id="claim_ticket")
    async def claim_ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(embed=discord.Embed(description=f"✅ Ticket claimed by {interaction.user.mention}!", color=discord.Color.green()))

    @discord.ui.button(label="Close Ticket 🔒", style=discord.ButtonStyle.danger, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("🔒 Generating transcript & closing ticket in 5 seconds...")
        
        try:
            messages = [f"--- TICKET TRANSCRIPT ({interaction.channel.name}) ---", f"Closed by: {interaction.user} at {datetime.now(timezone.utc)}", ""]
            async for msg in interaction.channel.history(limit=None, oldest_first=True):
                timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
                content = msg.content or "[Embed / Attachment]"
                messages.append(f"[{timestamp}] {msg.author}: {content}")
            
            transcript_content = "\n".join(messages)
            file = discord.File(io.BytesIO(transcript_content.encode('utf-8')), filename=f"transcript-{interaction.channel.name}.txt")
            
            global ticket_log_channel_id
            if ticket_log_channel_id:
                log_chan = interaction.guild.get_channel(ticket_log_channel_id)
                if log_chan:
                    embed_log = discord.Embed(title=f"🔒 Ticket Closed: {interaction.channel.name}", description=f"Closed by {interaction.user.mention}", color=discord.Color.orange(), timestamp=datetime.now(timezone.utc))
                    await log_chan.send(embed=embed_log, file=file)
        except Exception as e:
            print(f"[TRANSCRIPT ERROR] {e}")

        await asyncio.sleep(5)
        await interaction.channel.delete(reason="Ticket Closed")


class BaseTicketModal(Modal):
    def __init__(self, title, category_name, fields, custom_inside_title=None, custom_inside_desc=None):
        super().__init__(title=title[:45])
        self.category_name = category_name
        self.custom_inside_title = custom_inside_title
        self.custom_inside_desc = custom_inside_desc
        self.inputs = []

        for f in fields:
            text_input = TextInput(
                label=f["label"][:45],
                placeholder=f.get("placeholder", "Type here..."),
                style=f.get("style", discord.TextStyle.paragraph),
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

        global ticket_support_role_id, ticket_category_id, custom_ticket_ping
        support_role = guild.get_role(ticket_support_role_id) if ticket_support_role_id else None
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        target_category = guild.get_channel(ticket_category_id) if ticket_category_id else None

        ticket_channel = await guild.create_text_channel(
            name=ticket_channel_name, 
            overwrites=overwrites, 
            category=target_category
        )
        
        role_mention = support_role.mention if support_role else "@here"
        final_ping = custom_ticket_ping.replace("{role}", role_mention).replace("{user}", user.mention)
        await ticket_channel.send(final_ping)

        embed_title = self.custom_inside_title if self.custom_inside_title else f"🎫 {self.category_name}"
        embed_desc = self.custom_inside_desc if self.custom_inside_desc else f"Welcome {user.mention}!\nOur support team will assist you shortly. Please provide your details below."
        embed_desc = embed_desc.replace("{user}", user.mention)

        embed = discord.Embed(
            title=embed_title,
            description=embed_desc,
            color=discord.Color.red()
        )
        for label, input_item in self.inputs:
            embed.add_field(name=f"📌 {label}", value=input_item.value or "N/A", inline=False)
        
        await ticket_channel.send(embed=embed, view=TicketControlView())
        await interaction.response.send_message(f"✅ Ticket created: {ticket_channel.mention}", ephemeral=True)


class DynamicCustomTicketView(View):
    def __init__(self, buttons_data, inside_title, inside_desc):
        super().__init__(timeout=None)
        for b_name, b_questions in buttons_data:
            btn = Button(label=b_name[:80], style=discord.ButtonStyle.danger, emoji="📩")
            async def cb(interaction: discord.Interaction, name=b_name, questions=b_questions):
                fields = [{"label": q, "placeholder": f"Enter {q}...", "style": discord.TextStyle.paragraph} for q in questions]
                await interaction.response.send_modal(BaseTicketModal(name, name, fields, inside_title, inside_desc))
            btn.callback = cb
            self.add_item(btn)


# ==================== GIVEAWAY SYSTEM CLASSES ====================

class GiveawayJoinView(View):
    def __init__(self, giveaway_id):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id

    @discord.ui.button(label="🎉 Join Giveaway", style=discord.ButtonStyle.success, custom_id="join_giveaway")
    async def join_giveaway(self, interaction: discord.Interaction, button: Button):
        if self.giveaway_id not in active_giveaways:
            await interaction.response.send_message("❌ This giveaway has ended!", ephemeral=True)
            return
        
        participants = active_giveaways[self.giveaway_id]["participants"]
        if interaction.user.id in participants:
            await interaction.response.send_message("⚠️ You have already joined this giveaway!", ephemeral=True)
        else:
            participants.append(interaction.user.id)
            await interaction.response.send_message("✅ Successfully joined the giveaway! Best of luck! 🍀", ephemeral=True)


# ==================== INTERACTIVE SETUP WIZARD & EDIT MODALS ====================

class SetupWizardModal(Modal):
    def __init__(self, step, user_id):
        super().__init__(title="Ticket Panel Setup Wizard")
        self.step = step
        self.user_id = user_id

        if step == "title_desc":
            self.panel_title = TextInput(label="Panel Title", placeholder="e.g. Server Support", default="Help & Support", max_length=100)
            self.panel_desc = TextInput(label="Panel Description", placeholder="Enter welcome note...", style=discord.TextStyle.paragraph, default="Welcome to support!")
            self.add_item(self.panel_title)
            self.add_item(self.panel_desc)
        elif step == "button_info":
            self.btn_name = TextInput(label="Button Name", placeholder="e.g. Buy Rank / Bug Report", max_length=80)
            self.btn_questions = TextInput(label="Questions (Comma separated)", placeholder="e.g. IGN, Rank, Proof link", style=discord.TextStyle.paragraph)
            self.add_item(self.btn_name)
            self.add_item(self.btn_questions)
        elif step == "inside_ticket_info":
            self.inside_title = TextInput(label="Inside Ticket Title", placeholder="e.g. 🎫 Support Ticket", default="🎫 Support Ticket", max_length=100)
            self.inside_desc = TextInput(label="Inside Ticket Description", placeholder="e.g. Welcome {user}, team will help!", style=discord.TextStyle.paragraph, default="Welcome {user}!\nOur support team will assist you shortly.")
            self.add_item(self.inside_title)
            self.add_item(self.inside_desc)

    async def on_submit(self, interaction: discord.Interaction):
        data = setup_wizards.get(self.user_id)
        if not data:
            await interaction.response.send_message("❌ Setup session expired. Please run `/setup_ticket` again.", ephemeral=True)
            return

        if self.step == "title_desc":
            data["title"] = self.panel_title.value
            data["desc"] = self.panel_desc.value
            
            class NextStepView(View):
                def __init__(self, uid):
                    super().__init__(timeout=300)
                    self.uid = uid

                @discord.ui.button(label="Add Button & Options ➕", style=discord.ButtonStyle.success)
                async def add_btn(self, i: discord.Interaction, b: Button):
                    await i.response.send_modal(SetupWizardModal("button_info", self.uid))

            await interaction.response.send_message("📝 Title & Description saved! Click below to add support buttons:", view=NextStepView(self.user_id), ephemeral=True)

        elif self.step == "button_info":
            b_name = self.btn_name.value
            q_list = [q.strip() for q in self.btn_questions.value.split(",") if q.strip()]
            data["buttons"].append((b_name, q_list))

            class AddMoreOrInsideView(View):
                def __init__(self, uid):
                    super().__init__(timeout=300)
                    self.uid = uid

                @discord.ui.button(label="Add Another Button ➕", style=discord.ButtonStyle.success)
                async def add_more(self, i: discord.Interaction, b: Button):
                    await i.response.send_modal(SetupWizardModal("button_info", self.uid))

                @discord.ui.button(label="Next: Inside Ticket Details ➡️", style=discord.ButtonStyle.primary)
                async def next_inside(self, i: discord.Interaction, b: Button):
                    await i.response.send_modal(SetupWizardModal("inside_ticket_info", self.uid))

            await interaction.response.send_message(f"✅ Button **'{b_name}'** added! Add another button or proceed:", view=AddMoreOrInsideView(self.user_id), ephemeral=True)

        elif self.step == "inside_ticket_info":
            data["inside_title"] = self.inside_title.value
            data["inside_desc"] = self.inside_desc.value

            d = setup_wizards.pop(self.user_id, None)
            if not d:
                await interaction.response.send_message("❌ Session error.", ephemeral=True)
                return
            
            embed = discord.Embed(title=d["title"], description=d["desc"], color=discord.Color.from_rgb(230, 230, 210))
            embed.set_footer(text=f"Powered by {bot.user.name}")
            
            view = DynamicCustomTicketView(d["buttons"], d["inside_title"], d["inside_desc"])
            msg = await interaction.channel.send(embed=embed, view=view)
            
            active_ticket_panels[msg.id] = d

            await interaction.response.send_message(f"✅ Ticket Panel created successfully! Panel ID: `{msg.id}` (Use `/edit_ticket` with this ID to modify it later without rebuilding).", ephemeral=True)


class EditTicketModal(Modal):
    def __init__(self, message_id, panel_data):
        super().__init__(title="Edit Ticket Panel")
        self.message_id = message_id
        self.panel_data = panel_data

        self.panel_title = TextInput(label="New Panel Title", default=panel_data["title"], max_length=100)
        self.panel_desc = TextInput(label="New Panel Description", style=discord.TextStyle.paragraph, default=panel_data["desc"])
        self.inside_title = TextInput(label="New Inside Ticket Title", default=panel_data["inside_title"], max_length=100)
        self.inside_desc = TextInput(label="New Inside Ticket Description", style=discord.TextStyle.paragraph, default=panel_data["inside_desc"])

        self.add_item(self.panel_title)
        self.add_item(self.panel_desc)
        self.add_item(self.inside_title)
        self.add_item(self.inside_desc)

    async def on_submit(self, interaction: discord.Interaction):
        self.panel_data["title"] = self.panel_title.value
        self.panel_data["desc"] = self.panel_desc.value
        self.panel_data["inside_title"] = self.inside_title.value
        self.panel_data["inside_desc"] = self.inside_desc.value

        try:
            msg = await interaction.channel.fetch_message(self.message_id)
            embed = discord.Embed(title=self.panel_data["title"], description=self.panel_data["desc"], color=discord.Color.from_rgb(230, 230, 210))
            embed.set_footer(text=f"Powered by {bot.user.name}")
            view = DynamicCustomTicketView(self.panel_data["buttons"], self.panel_data["inside_title"], self.panel_data["inside_desc"])
            await msg.edit(embed=embed, view=view)
            await interaction.response.send_message("✅ Ticket Panel successfully updated!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to update panel message. Ensure command is run in the same channel. Error: {e}", ephemeral=True)


# ==================== WELCOME SETUP WIZARD ====================

class WelcomeSetupModal(Modal):
    def __init__(self):
        super().__init__(title="Welcome System Setup")
        self.wel_msg = TextInput(label="Welcome Message", style=discord.TextStyle.paragraph, default="Hey {user}, welcome to **{server}**! Member count: {count}", max_length=1000)
        self.wel_img = TextInput(label="Banner Image URL (Leave blank for user avatar)", required=False, default="")
        self.add_item(self.wel_msg)
        self.add_item(self.wel_img)

    async def on_submit(self, interaction: discord.Interaction):
        global custom_welcome_msg, custom_welcome_img, welcome_enabled
        custom_welcome_msg = self.wel_msg.value
        custom_welcome_img = self.wel_img.value if self.wel_img.value.strip() else None
        welcome_enabled = True
        await interaction.response.send_message(f"✅ **Welcome System fully configured and enabled!**\nMessage: `{custom_welcome_msg}`", ephemeral=True)


# ==================== BOT READY & INSTANT SLASH SYNC ====================

@bot.event
async def on_ready():
    print(f"✅ Bot Online & Ready: {bot.user}")
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


# ==================== SLASH COMMANDS ====================

@bot.tree.command(name="setup_ticket", description="Interactive Step-by-Step Ticket Panel Setup Wizard.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setup_ticket(
    interaction: discord.Interaction, 
    role: discord.Role = None, 
    category: discord.CategoryChannel = None
):
    if not is_whitelisted(interaction.user, interaction.guild) and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ **Access Denied:** You need administrator permissions.", ephemeral=True)
        return

    global ticket_support_role_id, ticket_category_id
    if role: ticket_support_role_id = role.id
    if category: ticket_category_id = category.id

    setup_wizards[interaction.user.id] = {"title": "", "desc": "", "buttons": [], "inside_title": "", "inside_desc": ""}
    await interaction.response.send_modal(SetupWizardModal("title_desc", interaction.user.id))


@bot.tree.command(name="edit_ticket", description="Directly edit an existing ticket panel without rebuilding.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_edit_ticket(interaction: discord.Interaction, message_id: str):
    if not is_whitelisted(interaction.user, interaction.guild) and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ **Access Denied:** You need administrator permissions.", ephemeral=True)
        return

    try:
        m_id = int(message_id)
    except ValueError:
        await interaction.response.send_message("❌ Please provide a valid numeric message ID.", ephemeral=True)
        return

    panel_data = active_ticket_panels.get(m_id)
    if not panel_data:
        await interaction.response.send_message("❌ Panel ID not found in active session cache. Make sure it was created with the new setup wizard.", ephemeral=True)
        return

    await interaction.response.send_modal(EditTicketModal(m_id, panel_data))


@bot.tree.command(name="giveaway", description="Start a giveaway with optional custom/fixed winner.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_giveaway(
    interaction: discord.Interaction, 
    prize: str, 
    duration: str, 
    winners_count: int = 1, 
    fixed_winner: discord.Member = None
):
    if not is_whitelisted(interaction.user, interaction.guild) and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ **Access Denied:** You need administrator permissions.", ephemeral=True)
        return

    seconds = parse_time(duration)
    embed = discord.Embed(
        title="🎉 GIVEAWAY 🎉",
        description=f"**Prize:** {prize}\n**Hosted by:** {interaction.user.mention}\n**Winners:** `{winners_count}`\n\nClick the button below to participate!",
        color=discord.Color.gold()
    )
    embed.set_footer(text=f"Ends in {duration}")

    g_id = random.randint(10000, 99999)
    active_giveaways[g_id] = {"participants": [], "prize": prize, "fixed_winner": fixed_winner}

    await interaction.response.send_message(embed=embed, view=GiveawayJoinView(g_id))
    msg = await interaction.original_response()

    await asyncio.sleep(seconds)

    g_data = active_giveaways.pop(g_id, None)
    if not g_data:
        return

    participants = g_data["participants"]
    chosen_winners = []

    if fixed_winner and fixed_winner.id in participants:
        chosen_winners.append(fixed_winner)
        participants.remove(fixed_winner.id)

    while len(chosen_winners) < winners_count and participants:
        winner_id = random.choice(participants)
        participants.remove(winner_id)
        member = interaction.guild.get_member(winner_id)
        if member:
            chosen_winners.append(member)

    if chosen_winners:
        winners_mention = ", ".join([w.mention for w in chosen_winners])
        result_embed = discord.Embed(
            title="🎉 GIVEAWAY ENDED 🎉",
            description=f"**Prize:** {prize}\n**Winner(s):** {winners_mention} 🏆",
            color=discord.Color.green()
        )
        await msg.edit(embed=result_embed, view=None)
        await interaction.channel.send(f"🎊 Congratulations {winners_mention}! You won **{prize}**!")
    else:
        result_embed = discord.Embed(
            title="🎉 GIVEAWAY ENDED 🎉",
            description=f"**Prize:** {prize}\n❌ No valid participants found.",
            color=discord.Color.red()
        )
        await msg.edit(embed=result_embed, view=None)


@bot.tree.command(name="set_ban_limit", description="Set anti-nuke max ban threshold limit.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_set_ban_limit(interaction: discord.Interaction, limit: int):
    if not is_whitelisted(interaction.user, interaction.guild) and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ **Access Denied:** You need administrator permissions.", ephemeral=True)
        return
    global ban_limit
    ban_limit = limit
    await interaction.response.send_message(f"✅ **Anti-Nuke Ban Limit updated to:** `{ban_limit}` bans / 2 mins")


@bot.tree.command(name="set_channel_limit", description="Set anti-nuke max channel delete threshold limit.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_set_channel_limit(interaction: discord.Interaction, limit: int):
    if not is_whitelisted(interaction.user, interaction.guild) and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ **Access Denied:** You need administrator permissions.", ephemeral=True)
        return
    global channel_limit
    channel_limit = limit
    await interaction.response.send_message(f"✅ **Anti-Nuke Channel Delete Limit updated to:** `{channel_limit}` channels / 2 mins")


@bot.tree.command(name="set_spam_limit", description="Set max allowed messages within 5 seconds before mute.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_set_spam_limit(interaction: discord.Interaction, messages_count: int):
    if not is_whitelisted(interaction.user, interaction.guild) and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ **Access Denied:** You need administrator permissions.", ephemeral=True)
        return
    global spam_limit
    spam_limit = messages_count
    await interaction.response.send_message(f"✅ **Anti-Spam Limit updated to:** `{spam_limit}` msgs / 5 sec")


@bot.tree.command(name="set_prefix", description="Set custom prefix for text commands.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_set_prefix(interaction: discord.Interaction, prefix: str):
    if not is_whitelisted(interaction.user, interaction.guild) and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ **Access Denied:** You need administrator permissions.", ephemeral=True)
        return
    global custom_prefix
    custom_prefix = prefix
    await interaction.response.send_message(f"✅ **Custom Prefix updated to:** `{custom_prefix}`")


@bot.tree.command(name="role", description="Assign or remove a role from a member easily.")
@app_commands.checks.has_permissions(manage_roles=True)
async def slash_role(interaction: discord.Interaction, action: Literal["add", "remove"], member: discord.Member, role: discord.Role):
    if not is_whitelisted(interaction.user, interaction.guild) and not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("❌ **Access Denied:** You lack role management permissions.", ephemeral=True)
        return
    if action == "add":
        await member.add_roles(role, reason=f"Managed by {interaction.user}")
        await interaction.response.send_message(f"✅ Successfully added **{role.name}** to {member.mention}!")
    else:
        await member.remove_roles(role, reason=f"Managed by {interaction.user}")
        await interaction.response.send_message(f"✅ Successfully removed **{role.name}** from {member.mention}!")


@bot.tree.command(name="ticket_log_channel", description="Set log channel for closed ticket transcripts.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_ticket_log_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_whitelisted(interaction.user, interaction.guild) and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ **Access Denied:** You need administrator permissions.", ephemeral=True)
        return
    global ticket_log_channel_id
    ticket_log_channel_id = channel.id
    await interaction.response.send_message(f"✅ **Ticket Transcript Log Channel set to:** {channel.mention}")


@bot.tree.command(name="set_ticket_ping", description="Customize ticket opening ping message.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_set_ticket_ping(interaction: discord.Interaction, message: str):
    if not is_whitelisted(interaction.user, interaction.guild) and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ **Access Denied:** You need administrator permissions.", ephemeral=True)
        return
    global custom_ticket_ping
    custom_ticket_ping = message
    await interaction.response.send_message(f"✅ **Ticket Open Ping updated!**\nFormat: `{message}`")


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
    embed.add_field(name="/setup_ticket [role] [category]", value="Launch interactive setup wizard for clean ticket panel UI", inline=False)
    embed.add_field(name="/edit_ticket <message_id>", value="Directly edit existing panel without rebuilding", inline=False)
    embed.add_field(name="/ticket_log_channel <channel>", value="Set channel for closed ticket text transcripts", inline=False)
    embed.add_field(name="/set_ticket_ping <msg>", value="Set custom ticket mention message using {role} & {user}", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="giveaway_help", description="Show all Giveaway system commands.")
async def help_giveaway(interaction: discord.Interaction):
    embed = discord.Embed(title="🎉 Giveaway System Commands", color=discord.Color.gold())
    embed.add_field(name="/giveaway <prize> <duration> [winners] [fixed_winner]", value="Start giveaway with optional fixed/custom winner", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="welcome_help", description="Show all Welcome System commands.")
async def help_welcome(interaction: discord.Interaction):
    embed = discord.Embed(title="👋 Welcome System Commands", color=discord.Color.green())
    embed.add_field(name="/setup_welcome [channel]", value="Set welcome channel", inline=False)
    embed.add_field(name="/setup_welcome_wizard", value="Interactive modal setup for welcome msg & banner image", inline=False)
    embed.add_field(name="/set_welcomemsg <msg>", value="Set custom text with tags {user},{server},{count},{inviter}", inline=False)
    embed.add_field(name="/set_welcomeimg <url>", value="Set banner Image URL", inline=False)
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
    embed.add_field(name="/role <add/remove> <member> <role>", value="Quickly assign or remove roles", inline=False)
    embed.add_field(name="/set_prefix <prefix>", value="Change bot command prefix", inline=False)
    embed.add_field(name="/dmall <message>", value="Broadcast announcement via DMs", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="setup_invitelog", description="Set channel for invite logs.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setup_invitelog(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not is_whitelisted(interaction.user, interaction.guild) and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ **Access Denied:** You need administrator permissions.", ephemeral=True)
        return
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
@app_commands.describe(message="The message you want to broadcast", as_embed="True for Embed format, False for Plain Text")
@app_commands.checks.has_permissions(administrator=True)
async def slash_dmall(interaction: discord.Interaction, message: str, as_embed: bool = False):
    if not is_whitelisted(interaction.user, interaction.guild) and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ **Access Denied:** You need administrator permissions.", ephemeral=True)
        return
        
    format_type = "Embed" if as_embed else "Plain Text"
    await interaction.response.send_message(f"⏳ **Starting DM Broadcast ({format_type})...** Safe delay active.")
    success_count, failed_count = 0, 0

    for member in interaction.guild.members:
        if member.bot: continue
        try:
            if as_embed:
                embed = discord.Embed(title=f"📢 Announcement from {interaction.guild.name}", description=message, color=discord.Color.gold())
                await member.send(embed=embed)
            else:
                await member.send(content=message)
                
            success_count += 1
            await asyncio.sleep(1.5)
        except Exception:
            failed_count += 1

    await interaction.followup.send(f"✅ Sent ({format_type}): {success_count} | ❌ Failed: {failed_count}")



class WelcomeSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.select(cls=discord.ui.ChannelSelect, placeholder="📌 Select Welcome Channel...", channel_types=[discord.ChannelType.text], min_values=1, max_values=1)
    async def select_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        selected_channel = select.values[0]
        global welcome_channel_id
        welcome_channel_id = selected_channel.id
        
        # Channel select hone ke turant baad modal khulega message aur banner ke liye
        await interaction.response.send_modal(SimpleWelcomeModal())


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


@bot.tree.command(name="setup_welcome", description="Configure custom welcome channel, message and banner.")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setup_welcome(interaction: discord.Interaction):
    if not is_whitelisted(interaction.user, interaction.guild) and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ **Access Denied:** You need administrator permissions.", ephemeral=True)
        return
    
    # Jaise hi command chalayega, channel select karne ka dropdown aayega
    view = WelcomeSelectView()
    await interaction.response.send_message("📌 **Please select your welcome channel from the dropdown below:**", view=view, ephemeral=True)

@bot.tree.command(name="ban", description="Ban a member.")
@app_commands.checks.has_permissions(ban_members=True)
async def slash_ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not is_whitelisted(interaction.user, interaction.guild) and not interaction.user.guild_permissions.ban_members:
        await interaction.response.send_message("❌ **Access Denied:** You lack ban permissions.", ephemeral=True)
        return
    if is_whitelisted(member, interaction.guild):
        await interaction.response.send_message("❌ **Access Denied:** User is whitelisted.", ephemeral=True)
        return
    await member.ban(reason=reason)
    await interaction.response.send_message(f"🔨 **{member.mention} banned!**")


@bot.tree.command(name="mute", description="Timeout a member.")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_mute(interaction: discord.Interaction, member: discord.Member, duration: str, reason: str = "No reason provided"):
    if not is_whitelisted(interaction.user, interaction.guild) and not interaction.user.guild_permissions.moderate_members:
        await interaction.response.send_message("❌ **Access Denied:** You lack timeout permissions.", ephemeral=True)
        return
    if is_whitelisted(member, interaction.guild):
        await interaction.response.send_message("❌ **Access Denied:** User is whitelisted.", ephemeral=True)
        return
    seconds = parse_time(duration)
    await member.timeout(timedelta(seconds=seconds), reason=reason)
    await interaction.response.send_message(f"🤐 **{member.mention} timed out for {duration}!**")


@bot.tree.command(name="purge", description="Bulk delete messages in current channel.")
@app_commands.checks.has_permissions(manage_messages=True)
async def slash_purge(interaction: discord.Interaction, amount: int):
    if not is_whitelisted(interaction.user, interaction.guild) and not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("❌ **Access Denied:** You lack message management permissions.", ephemeral=True)
        return
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
