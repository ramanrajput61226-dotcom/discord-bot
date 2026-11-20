"""Source-level regression checks for Agni's critical safety guarantees."""
from pathlib import Path
import ast

SOURCE = Path(__file__).resolve().parents[1] / "ravagerazebot.py"


def read_source():
    return SOURCE.read_text(encoding="utf-8")


def test_bot_source_parses():
    ast.parse(read_source())


def test_ticket_transcript_targets_opener_not_closer():
    source = read_source()
    start = source.index("class CloseTicketModal")
    end = source.index("class ManagerBaseView", start)
    close_flow = source[start:end]
    assert "opener.send(" in close_flow
    assert "closer.send(" not in close_flow
    assert "agni_ticket_owner" in close_flow


def test_ticket_close_does_not_require_dm_or_log_delivery():
    source = read_source()
    start = source.index("class CloseTicketModal")
    end = source.index("class ManagerBaseView", start)
    close_flow = source[start:end]
    assert "Transcript delivery is best-effort" in close_flow
    assert "channel.delete(reason=" in close_flow
    assert "Manage Channels permission" in close_flow


def test_welcome_database_columns_are_migrated():
    source = read_source()
    assert 'ADD COLUMN welcome_channel_id INTEGER' in source
    assert 'ADD COLUMN ticket_log_channel_id INTEGER' in source


def test_welcome_has_simple_edit_commands():
    source = read_source()
    for command in ("welcome_channel", "welcome_message", "welcome_image", "enable_welcome", "welcome_status"):
        assert f'name="{command}"' in source


def test_failed_ticket_creation_cleans_up_orphan_channel():
    source = read_source()
    start = source.index("async def create_ticket")
    end = source.index("class DynamicTicketModal", start)
    create_flow = source[start:end]
    assert "channel = None" in create_flow
    assert "cleaning up orphan channel" in create_flow


def test_legacy_unconditional_spam_timeout_is_removed():
    source = read_source()
    assert "Anti-Spam: Sending messages too quickly." not in source


def test_anti_nuke_tracking_is_scoped_by_guild_and_executor():
    source = read_source()
    assert "nuke_tracking[executor.id]" not in source
    assert "nuke_tracking[(guild.id, executor.id)]" in source


def test_economy_transfer_uses_atomic_conditional_debit():
    source = read_source()
    start = source.index("async def pay_cmd")
    end = source.index("# Invite statistics", start)
    pay_flow = source[start:end]
    assert 'DB.execute("BEGIN IMMEDIATE")' in pay_flow
    assert "coins >= ?" in pay_flow
    assert "DB.rollback()" in pay_flow


def test_moderation_thresholds_are_per_guild():
    source = read_source()
    assert 'ADD COLUMN nuke_ban_limit INTEGER DEFAULT 5' in source
    assert 'ADD COLUMN nuke_channel_limit INTEGER DEFAULT 3' in source
    assert 'ADD COLUMN automod_spam_limit INTEGER DEFAULT 5' in source
    assert 'get_guild_setting(guild.id, "nuke_ban_limit", ban_limit)' in source
    assert 'get_guild_setting(guild.id, "nuke_channel_limit", channel_limit)' in source


def test_help_menu_is_generated_from_registered_commands():
    source = read_source()
    start = source.index('@bot.hybrid_command(name="help"')
    end = source.index("# Register persistent verification", start)
    help_flow = source[start:end]
    assert "for command in bot.commands" in help_flow
    assert "Available commands" in help_flow


def test_live_database_backups_are_not_embedded_in_source():
    source = read_source()
    assert "_AGNI_EMBEDDED_DB" not in source
    assert "_BACKUP_DB_B64" not in source
    assert "_BACKUP_CONFIG_B64" not in source
    assert "restore_supplied_backup_if_missing" not in source


def test_giveaways_persist_end_time_and_resume_after_restart():
    source = read_source()
    assert "ends_at TEXT" in source
    assert 'ALTER TABLE giveaways ADD COLUMN ends_at TEXT' in source
    assert "@tasks.loop(seconds=15)" in source
    assert "giveaway_recovery_task.start()" in source
    start = source.index("# GIVEAWAY — PERSISTENT, RESTART-SAFE")
    end = source.index("# RAID PROTECTION", start)
    giveaway_flow = source[start:end]
    assert "await asyncio.sleep(seconds)" not in giveaway_flow
    assert "ends_at<=?" in giveaway_flow
    assert "async def finish_giveaway" in giveaway_flow


def test_fixed_winner_tool_is_owner_only_test_mode_and_hidden():
    source = read_source()
    start = source.index('@bot.command(name="giveawaytest")')
    end = source.index('@bot.hybrid_command(name="reroll"', start)
    test_command = source[start:end]
    assert "ctx.author.id != OWNER_ID" in test_command
    assert "test_mode" in test_command
    assert "TEST GIVEAWAY — NO REAL PRIZE" in test_command
    assert "giveaway_test_cmd.hidden = True" in test_command
    assert "giveawayfixed" not in source
    assert "Fixed winner:" not in source


def test_anti_nuke_matches_fresh_audit_entries_to_exact_target():
    source = read_source()
    start = source.index("async def _find_recent_audit_executor")
    end = source.index("@bot.event", start)
    helper = source[start:end]
    assert "entry.target" in helper
    assert "target_id" in helper
    assert "age <= 15" in helper
    assert "guild.audit_logs(limit=8" in helper


def test_anti_nuke_tracks_role_deletions_with_per_guild_threshold():
    source = read_source()
    assert 'ADD COLUMN nuke_role_limit INTEGER DEFAULT 3' in source
    assert 'name="set_role_limit"' in source
    role_start = source.index("async def on_guild_role_delete")
    role_end = source.index("async def on_guild_role_update", role_start)
    role_flow = source[role_start:role_end]
    assert "AuditLogAction.role_delete" in role_flow
    assert "_record_nuke_action" in role_flow
    assert 'get_guild_setting(role.guild.id, "nuke_role_limit", 3)' in role_flow
