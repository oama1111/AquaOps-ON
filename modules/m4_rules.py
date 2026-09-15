"""M4 — O. Reg. 170/03 rule engine: versioned YAML → required tasks (ADR-2)."""

from __future__ import annotations

from pathlib import Path

from app.schemas.rules import RuleOut, TaskBatch

RULES_PATH = Path("rules/oreg170.yaml")


def load_rules(yaml_path: Path = RULES_PATH) -> list[RuleOut]:
    """Load the rule catalogue; rules with verified:false load as inert."""
    raise NotImplementedError


def generate_tasks(system_id: str, week: str, rules: list[RuleOut]) -> TaskBatch:
    """Expand verified rules into windowed sampling tasks for the horizon."""
    raise NotImplementedError


def hard_constraint_window(rule_id: str, week: str) -> tuple[str, str]:
    """Regulatory window for a zero-gap rule; a miss is a violation."""
    raise NotImplementedError
