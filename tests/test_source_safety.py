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
