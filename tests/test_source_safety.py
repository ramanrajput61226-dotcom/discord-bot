"""Source-level regression checks for critical ticket safety guarantees."""
from pathlib import Path
import ast

SOURCE = Path(__file__).resolve().parents[1] / "ravagerazebot.py"


def test_bot_source_parses():
    ast.parse(SOURCE.read_text(encoding="utf-8"))


def test_ticket_transcript_targets_opener_not_closer():
    source = SOURCE.read_text(encoding="utf-8")
    start = source.index("class CloseTicketModal")
    end = source.index("class ManagerBaseView", start)
    close_flow = source[start:end]
    assert "opener.send(" in close_flow
    assert "closer.send(" not in close_flow
    assert "agni_ticket_owner" in close_flow


def test_ticket_is_not_deleted_if_transcript_delivery_fails():
    source = SOURCE.read_text(encoding="utf-8")
    start = source.index("class CloseTicketModal")
    end = source.index("class ManagerBaseView", start)
    close_flow = source[start:end]
    assert "if not delivered:" in close_flow
    assert "The ticket has NOT been deleted" in close_flow
