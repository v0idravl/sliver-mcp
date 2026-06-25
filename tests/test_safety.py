"""Unit tests for the noise / arm_dangerous safety model."""

from sliver_mcp.safety import SafetyState


def test_default_ceiling_is_green():
    s = SafetyState()
    assert s.ceiling == "green"
    assert s.armed is False


def test_passive_and_green_allowed_by_default():
    s = SafetyState()
    assert s.check("passive", False)[0] is True
    assert s.check("green", False)[0] is True


def test_yellow_blocked_until_raised():
    s = SafetyState()
    allowed, reason = s.check("yellow", False)
    assert allowed is False
    assert "ceiling" in reason
    s.set_noise("yellow")
    assert s.check("yellow", False)[0] is True


def test_red_locked_without_arm():
    s = SafetyState()
    ok, reason = s.set_noise("red")
    assert ok is False
    assert "arm_dangerous" in reason


def test_arm_unlocks_red():
    s = SafetyState()
    s.arm()
    assert s.armed is True
    assert s.ceiling == "red"
    assert s.check("red", True)[0] is True


def test_destructive_requires_armed_even_at_red_ceiling():
    s = SafetyState()
    # Manually push ceiling to red is impossible without arm; arm sets both.
    s.arm()
    s.armed = False  # simulate ceiling red but not armed
    allowed, reason = s.check("red", True)
    assert allowed is False
    assert "arm_dangerous" in reason


def test_invalid_level_rejected():
    s = SafetyState()
    ok, reason = s.set_noise("loud")
    assert ok is False
    assert "one of" in reason
