"""Regression checks for the secure web control panel."""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_module_parses():
    ast.parse((ROOT / "dashboard.py").read_text(encoding="utf-8"))


def test_dashboard_uses_discord_oauth_and_server_permissions():
    source = (ROOT / "dashboard.py").read_text(encoding="utf-8")
    assert 'scope": "identify guilds"' in source
    assert "permissions & 0x20 or permissions & 0x8" in source
    assert "Agni is not currently in this server." in source


def test_dashboard_never_sends_bot_token_to_browser():
    source = (ROOT / "dashboard.py").read_text(encoding="utf-8")
    assert 'headers["Authorization"] = "Bot " + os.getenv("DISCORD_TOKEN", "")' in source
    assert 'headers["Authorization"] = "Bearer " + token' in source
    assert 'SESSION_COOKIE_HTTPONLY=True' in source
    assert 'SESSION_COOKIE_SAMESITE="Lax"' in source


def test_dashboard_settings_are_allowlisted_and_validated():
    source = (ROOT / "dashboard.py").read_text(encoding="utf-8")
    assert "if key not in ALLOWED or key not in columns:" in source
    assert "Selected channel or role does not belong to this server." in source
    assert "Numeric settings must be between 0 and 100000." in source


def test_dashboard_exposes_only_managed_servers_with_bot_installed():
    source = (ROOT / "dashboard.py").read_text(encoding="utf-8")
    assert "allowed = [g for g in guilds" in source
    assert "_bot.get_guild(int(g[\"id\"]))" in source
