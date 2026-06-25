"""Noise tiers and the ``arm_dangerous`` gate.

Ports p0rtix's posture model so the Sliver MCP enforces the same discipline the
dagar-red skills already assume: every tool carries a noise *tier*, a call above
the current ceiling is refused with a structured reason (never silently
downgraded), and destructive actions stay locked until explicitly armed.

Tiers, lowest to highest footprint:

* ``passive`` — read-only state (status, list_sessions, get_version).
* ``green``   — build/stand-up that touches our own infra (listeners, implants,
  ls/pwd/cd/download).
* ``yellow``  — actions on the target that generate host telemetry (execute,
  upload, kill).
* ``red``     — destructive (``rm``); requires ``arm_dangerous()``.

The default ceiling is ``green``: the agent must ``set_noise("yellow")`` before
running commands on a host, matching the sliver-ops loop's explicit noise step.
"""

from __future__ import annotations

TIERS: dict[str, int] = {"passive": 0, "green": 1, "yellow": 2, "red": 3}
DEFAULT_CEILING = "green"


class SafetyState:
    """Per-process noise ceiling + armed flag. One instance lives on the manager."""

    def __init__(self, ceiling: str = DEFAULT_CEILING) -> None:
        self.ceiling = ceiling if ceiling in TIERS else DEFAULT_CEILING
        self.armed = False

    # -- mutation -----------------------------------------------------------
    def set_noise(self, level: str) -> tuple[bool, str]:
        """Raise or lower the ceiling. RED cannot be reached without arming."""
        level = (level or "").lower()
        if level not in TIERS:
            return False, f"level must be one of {list(TIERS)}"
        if TIERS[level] >= TIERS["red"] and not self.armed:
            return False, "RED is locked — call arm_dangerous() first"
        self.ceiling = level
        return True, level

    def arm(self) -> None:
        """Unlock destructive (red) tools and raise the ceiling to red."""
        self.armed = True
        self.ceiling = "red"

    # -- enforcement --------------------------------------------------------
    def check(self, tier: str, requires_armed: bool) -> tuple[bool, str | None]:
        """Return ``(allowed, reason)`` for a tool at ``tier``."""
        if tier not in TIERS:  # programmer error — fail closed
            return False, f"unknown tier '{tier}'"
        if TIERS[tier] > TIERS[self.ceiling]:
            return False, (
                f"blocked: '{tier}'-tier action exceeds the current noise "
                f"ceiling '{self.ceiling}' — raise it with set_noise('{tier}')"
            )
        if requires_armed and not self.armed:
            return False, "blocked: destructive action requires arm_dangerous() first"
        return True, None

    def snapshot(self) -> dict:
        return {"noise": self.ceiling, "armed": self.armed}
