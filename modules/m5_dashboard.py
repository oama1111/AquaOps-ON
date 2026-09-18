"""M5 — operator dashboard & reporting: server-rendered HTMX fragments (ADR-4)."""

from __future__ import annotations

from pathlib import Path


def render_network_status(system_id: str) -> str:
    """Network map fragment with a per-point status chip. (`GET /dash/choropleth`)"""
    raise NotImplementedError


def render_alert_inbox(system_id: str) -> str:
    """Open-alert fragment, polled every 30 s. (`GET /dash/alerts`)"""
    raise NotImplementedError


def render_week_view(system_id: str, week_start: str) -> str:
    """This week's plan against its regulatory windows. (`GET /dash/week`)"""
    raise NotImplementedError


def export_weekly_summary(system_id: str, week_start: str, out_path: Path) -> Path:
    """Write the weekly compliance summary for municipal records."""
    raise NotImplementedError
