import discord
from discord.ext import commands
from discord.ui import Button, View
import asyncio
import random
from datetime import datetime, timedelta, timezone
from keep_alive import keep_alive
import os

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.moderation = True

bot = commands.Bot(command_prefix="!", intents=intents)

ban_tracker = {}
channel_tracker = {}
banned_members_history = []

ticket_support_role_id = None
rigged_winner_id = None
welcome_channel_id = None


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
    if unit == 's':
        return val
    if unit == 'm':
        return val * 60
    if unit == 'h':
        return val * 3600
    if unit == 'd':
        return val * 86400
    return int(time_str)


async def punish_nuker(guild, executor, reason):
    try:
        roles_to_remove = [role for role in executor.roles if role.name != "@everyone" and role < guild.me.top_role]
        if roles_to_remove:
            await executor.remove_roles(*roles_to_remove, reason=f"[ANTI-NUKE] {reason}")

        await executor.timeout(timedelta(days=1), reason=f"[ANTI-NUKE] {reason}")

        if guild.system_channel:
            embed = discord.Embed(
                title="🚨 ANTI-NUKE SYSTEM TRIGGERED!",
                description=f"**Attacker:** {executor.mention} (`{executor.id}`)\n**Reason:** {reason}\n**Action Taken:** Roles Stripped & 24-Hour Timeout Applied 🚫",
                color=discord.Color.red()
            )
            await guild.system_channel.send(embed=embed)
    except Exception as e:
        print(f"[ANTI-NUKE ERROR] Failed to punish nuker: {e}")


@bot.event
async def on_member_join(member):
    guild = member.guild
    global welcome_channel_id
    
    target_channel = None
    if welcome_channel_id:
        target_channel = guild.get_channel(welcome_channel_id)
    
    if not target_channel:
        target_channel = guild.system_channel

    if target_channel:
        embed = discord.Embed(
            title="👋 Welcome to the Server!",
            description=f"Hey {member.mention}, welcome to our team! We are glad to have you here. Make sure to check out the rules and enjoy your stay! 🎉",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Member #{guild.member_count}")
        
        await target_channel.send(content=f"Welcome to our team {member.mention}!", embed=embed)


@bot.command()
@commands.has_permissions(administrator=True)
async def setup_welcome(ctx, channel: discord.TextChannel = None):
    global welcome_channel_id
    target = channel or ctx.channel
    welcome_channel_id = target.id
    
    await ctx.send(f"✅ **Welcome channel set to:** {target.mention}")


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

        if len(user_bans) >= 5:
            await punish_nuker(guild, executor, "Mass Ban Attempt (5+ Bans in 2 Mins)")
            
            for member_id, ban_time in list(banned_members_history):
                if (now - ban_time).total_seconds() <= 120:
                    try:
                        ban_entry = await guild.fetch_ban(discord.Object(id=member_id))
                        await guild.unban(ban_entry.user, reason="[ANTI-NUKE REVERT] Restoring affected member")
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

        if len(user_deletes) >= 4:
            await punish_nuker(guild, executor, "Mass Channel Delete Attempt (4+ Channels in 2 Mins)")
            channel_tracker[executor.id] = []

        try:
            new_channel = await channel.clone(reason="[ANTI-NUKE RECREATE] Restoring deleted channel")
            await new_channel.edit(position=channel.position)
        except Exception as e:
            print(f"[ANTI-NUKE ERROR] Failed to recreate channel: {e}")
        break


@bot.command(name="ban")
@commands.has_permissions(ban_members=True)
async def ban_member(ctx, member: discord.Member, *, reason="No reason provided"):
    if is_whitelisted(member, ctx.guild):
        await ctx.send("❌ **Access Denied:** User is whitelisted or holds a higher/equal role.")
        return

    try:
        await member.ban(reason=f"[Ban by {ctx.author}] {reason}")
        await ctx.send(f"🔨 **{member.mention} has been permanently banned!** Reason: {reason}")
    except Exception as e:
        await ctx.send(f"❌ **Error:** {e}")


@bot.command(name="unban")
@commands.has_permissions(ban_members=True)
async def unban_member(ctx, user_id: int, *, reason="No reason provided"):
    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user, reason=f"[Unban by {ctx.author}] {reason}")
        await ctx.send(f"✅ **{user.name} (`{user_id}`) has been unbanned!**")
    except Exception as e:
        await ctx.send(f"❌ **Error:** Could not unban user. Make sure the ID is correct and they are banned.")


@bot.command(aliases=["mute", "timeout"])
@commands.has_permissions(moderate_members=True)
async def set_timeout(ctx, member: discord.Member, duration: str, *, reason="No reason provided"):
    if is_whitelisted(member, ctx.guild):
        await ctx.send("❌ **Access Denied:** Cannot timeout this user.")
        return

    try:
        seconds = parse_time(duration)
        await member.timeout(timedelta(seconds=seconds), reason=f"[Timeout by {ctx.author}] {reason}")
        await ctx.send(f"🤐 **{member.mention} has been timed out for {duration}!** Reason: {reason}")
    except Exception as e:
        await ctx.send(f"❌ **Error:** Use correct format e.g. `!mute @user 10m Reason` | {e}")


@bot.command(aliases=["unmute", "untimeout"])
@commands.has_permissions(moderate_members=True)
async def remove_timeout(ctx, member: discord.Member, *, reason="Timeout removed"):
    try:
        await member.timeout(None, reason=f"[Untimeout by {ctx.author}] {reason}")
        await ctx.send(f"🔊 **Timeout removed for {member.mention}!**")
    except Exception as e:
        await ctx.send(f"❌ **Error:** {e}")


@bot.command()
@commands.has_permissions(administrator=True)
async def rig(ctx, member: discord.Member):
    global rigged_winner_id
    rigged_winner_id = member.id
    await ctx.message.delete()
    await ctx.send(f"🤫 **Secret Winner Set:** {member.mention}", delete_after=5)


@bot.command()
@commands.has_permissions(administrator=True)
async def unrig(ctx):
    global rigged_winner_id
    rigged_winner_id = None
    await ctx.message.delete()
    await ctx.send("🤫 **Rigged winner cleared!** Next giveaway will be 100% random.", delete_after=5)


@bot.command()
@commands.has_permissions(administrator=True)
async def gstart(ctx, time_str: str, *, prize: str):
    global rigged_winner_id
    try:
        seconds = parse_time(time_str)
    except Exception:
        await ctx.send("❌ **Invalid time format!** Use `10s`, `5m`, `1h`, or `1d`.")
        return

    embed = discord.Embed(
        title="🎉 **GIVEAWAY STARTED!** 🎉",
        description=f"**Prize:** {prize}\n**Duration:** {time_str}\n\nReact with 🎉 to enter!",
        color=discord.Color.blue()
    )
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("🎉")

    await asyncio.sleep(seconds)

    new_msg = await ctx.channel.fetch_message(msg.id)
    reaction = discord.utils.get(new_msg.reactions, emoji="🎉")
    users = [user async for user in reaction.users() if not user.bot]

    winner = None
    if rigged_winner_id:
        rigged_user = discord.utils.get(users, id=rigged_winner_id)
        if rigged_user:
            winner = rigged_user
        rigged_winner_id = None

    if not winner and users:
        winner = random.choice(users)

    if winner:
        winner_mention = winner.mention
    else:
        winner_mention = "No valid participants!"

    end_embed = discord.Embed(
        title="🎉 **GIVEAWAY ENDED** 🎉",
        description=f"**Prize:** {prize}\n**Winner:** {winner_mention}",
        color=discord.Color.green()
    )
    await ctx.send(content=f"Congratulations {winner_mention}! You won the **{prize}**!", embed=end_embed)


class TicketControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim Ticket 🖐️", style=discord.ButtonStyle.success, custom_id="claim_ticket")
    async def claim_ticket(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            description=f"✅ **Ticket claimed by {interaction.user.mention}!** They will assist you shortly.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    @discord.ui.button(label="Close Ticket 🔒", style=discord.ButtonStyle.danger, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("🔒 Closing ticket in 5 seconds...")
        await asyncio.sleep(5)
        await interaction.channel.delete(reason="Ticket Closed")


class TicketOpenView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket 📩", style=discord.ButtonStyle.primary, custom_id="open_ticket")
    async def open_ticket(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        user = interaction.user

        ticket_channel_name = f"ticket-{user.name.lower()}"
        existing_channel = discord.utils.get(guild.channels, name=ticket_channel_name)
        if existing_channel:
            await interaction.response.send_message(f"❌ You already have an active ticket: {existing_channel.mention}", ephemeral=True)
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
        await ticket_channel.send(f"🔔 **New Ticket Created!** Attention {ping_text} - {user.mention} needs assistance.")

        embed = discord.Embed(
            title="🎫 Support Ticket Created",
            description=f"Welcome {user.mention}!\nPlease state your issue in detail. The support team will respond shortly.",
            color=discord.Color.blue()
        )
        await ticket_channel.send(embed=embed, view=TicketControlView())
        await interaction.response.send_message(f"✅ Ticket created: {ticket_channel.mention}", ephemeral=True)


@bot.command()
@commands.has_permissions(administrator=True)
async def setup_ticket(ctx, role: discord.Role = None):
    global ticket_support_role_id
    if role:
        ticket_support_role_id = role.id

    embed = discord.Embed(
        title="📩 Support & Help Desk",
        description="Need assistance or have a query? Click the button below to open a support ticket!",
        color=discord.Color.gold()
    )
    
    role_info = f"\n\n*Configured Ping Role:* {role.mention}" if role else ""
    embed.set_footer(text=f"Click the button below to contact staff.{role_info}")

    await ctx.send(embed=embed, view=TicketOpenView())
    await ctx.message.delete()


@bot.command(name="tempban")
@commands.has_permissions(ban_members=True)
async def tempban(ctx, member: discord.Member, duration: str, *, reason="Security Violations"):
    if is_whitelisted(member, ctx.guild):
        await ctx.send("❌ **Access Denied:** User is whitelisted or holds a higher/equal role.")
        return

    try:
        seconds = parse_time(duration)
        await member.ban(reason=f"[Temp-Ban by {ctx.author}] {reason}")
        await ctx.send(f"🔨 **{member.mention} has been temporarily banned for {duration}!**")

        await asyncio.sleep(seconds)

        await ctx.guild.unban(member, reason="Tempban Duration Expired")
        await ctx.send(f"✅ **{member.mention} has been automatically unbanned.**")
    except Exception as e:
        await ctx.send(f"❌ **Error:** {e}")


@bot.event
async def on_ready():
    print(f"✅ Bot Online & Ready: {bot.user}")
    bot.add_view(TicketOpenView())
    bot.add_view(TicketControlView())

keep_alive()
BOT_TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(BOT_TOKEN)
