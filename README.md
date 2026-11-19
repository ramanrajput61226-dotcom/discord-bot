# Agni Discord Bot

A multi-purpose Discord bot with moderation, AutoMod, anti-nuke/raid controls, tickets, giveaways, welcome/verification tools, invite tracking, logging, leveling, economy, and voice statistics.

## Requirements
- Python 3.10 or newer (Python 3.11/3.12 recommended)
- A Discord application and bot token

## Setup
1. Create a Discord application and add a bot in the Discord Developer Portal.
2. Enable **Server Members Intent** and **Message Content Intent**. Invite tracking also uses the Guild Invites intent.
3. Copy `.env.example` to `.env` and set `DISCORD_TOKEN`. Never publish the real token.
4. Install dependencies: `python -m pip install -r requirements.txt`
5. Start: `python ravagerazebot.py`

The bot reads `DISCORD_TOKEN` from the process environment. Hosting panels should store it in their Secrets/Environment settings. The example file is documentation; this bot does not automatically load dotenv files.

## Persistent storage
Server configuration and user data live in `agni_data.sqlite3`; optional JSON settings are in `bot_settings.json`, and scheduled backups are in `backups/`. Mount persistent storage at the working directory when deploying to an ephemeral host. Back up SQLite before upgrades. Never delete or overwrite an existing database to troubleshoot startup.

## Health endpoint
The optional Flask server starts alongside the bot. `/` returns service status JSON and `/health` returns HTTP 200 when the web process responds. This checks the web process, not Discord gateway connectivity. Set `PORT` if required by your host.

## Permissions and safety
Grant only the permissions required by enabled features. Typical moderation/ticket features need View Channels, Send Messages, Read Message History, Manage Channels, Manage Messages, Moderate Members and role-specific moderation permissions. The bot's highest role must be above roles it manages.

Configure independent log channels with `/setlog`. Welcome setup supports `/welcome_channel`, `/welcome_message`, `/welcome_image`, `/enable_welcome`, `/welcome_status`, and `/disable_welcome`. Message placeholders: `{user}`, `{server}`, `{count}`, `{inviter}`. Ticket transcript delivery uses the ticket log channel when configured, with the general log channel as fallback. Ticket transcript delivery is best-effort; authorized ticket closure still deletes the channel when the bot has Manage Channels permission. If that permission is missing, the bot reports the exact permission needed and keeps the channel safe.

## Validation
```bash
python -m pip install -r requirements-dev.txt
python -m py_compile ravagerazebot.py keep_alive.py
pytest -q
```
GitHub Actions runs syntax and regression checks on pushes to `main`/the upgrade branch and pull requests to `main`.

## Deployment notes
- Keep SQLite on persistent storage.
- Keep the token in environment secrets, never in source control.
- Enable required privileged intents in the Developer Portal.
- Use a process manager/hosting platform to restart the bot after crashes.
- Review moderation permissions and thresholds before enabling enforcement in a live server.
