from keep_alive import keep_alive
import discord
from discord.ext import commands
import asyncio
from datetime import datetime, timedelta
import random

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

DEFAULT_WINNER_ID = 1351536969934573610  

@bot.event
async def on_ready():
    print(f'✅ Bot Online: {bot.user}')

@bot.command()
@commands.has_permissions(administrator=True)
async def gstart(ctx, time_limit: str, target_user: str, *, prize: str):
    seconds = parse_time(time_limit)
    formatted_end = (datetime.now() + timedelta(seconds=seconds)).strftime("%d-%b-%Y %I:%M %p")

    if target_user.lower() == "default":
        winner_id = DEFAULT_WINNER_ID
    else:
        clean_id = target_user.replace("<@", "").replace(">", "").replace("!", "")
        winner_id = int(clean_id) if clean_id.isdigit() else DEFAULT_WINNER_ID

    embed = discord.Embed(
        title="🎉 **MEGA GIVEAWAY** 🎉",
        description=f"**Prize:** {prize}\n\nReact with 🎉 to enter!\n**Ends At:** `{formatted_end}`",
        color=discord.Color.gold()
    )
    embed.set_footer(text="Hosted by Server Staff • Official Event")

    msg = await ctx.send(embed=embed)
    await msg.add_reaction("🎉")

    await asyncio.sleep(seconds)

    try:
        winner = await bot.fetch_user(winner_id)
        winner_mention = winner.mention
    except:
        winner_mention = f"<@{winner_id}>"

    end_embed = discord.Embed(
        title="🎉 **GIVEAWAY ENDED** 🎉",
        description=f"**Prize:** {prize}\n**Winner:** {winner_mention}",
        color=discord.Color.green()
    )
    await ctx.send(content=f"Congratulations {winner_mention}! You won the **{prize}**!", embed=end_embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def grandom(ctx, time_limit: str, *, prize: str):
    seconds = parse_time(time_limit)
    formatted_end = (datetime.now() + timedelta(seconds=seconds)).strftime("%d-%b-%Y %I:%M %p")

    embed = discord.Embed(
        title="🎉 **GIVEAWAY (REAL RANDOM)** 🎉",
        description=f"**Prize:** {prize}\n\nReact with 🎉 to enter!\n**Ends At:** `{formatted_end}`",
        color=discord.Color.blue()
    )
    embed.set_footer(text="Hosted by Server Staff • Official Event")

    msg = await ctx.send(embed=embed)
    await msg.add_reaction("🎉")

    await asyncio.sleep(seconds)

    new_msg = await ctx.channel.fetch_message(msg.id)
    reaction = discord.utils.get(new_msg.reactions, emoji="🎉")
    users = [user async for user in reaction.users() if not user.bot]

    if users:
        winner = random.choice(users)
        winner_mention = winner.mention
    else:
        winner_mention = "No valid participants!"

    end_embed = discord.Embed(
        title="🎉 **GIVEAWAY ENDED** 🎉",
        description=f"**Prize:** {prize}\n**Winner:** {winner_mention}",
        color=discord.Color.green()
    )
    await ctx.send(content=f"Congratulations {winner_mention}! You won the **{prize}**!", embed=end_embed)

def parse_time(time_str):
    unit = time_str[-1].lower()
    val = int(time_str[:-1])
    if unit == 's': return val
    if unit == 'm': return val * 60
    if unit == 'h': return val * 3600
    if unit == 'd': return val * 86400
    return int(time_str)

import os

keep_alive()
BOT_TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(BOT_TOKEN)
