"""Global rate limiter behaviour."""
from app.core.rate_limiter import check_rate_limit, RATE_LIMITS


def test_rate_limit_allows_within_burst():
    key = "test:within-burst"
    result = check_rate_limit(key, "free")
    assert result["allowed"] is True


def test_rate_limit_triggers_after_burst():
    """Free tier allows 10 per burst window; the 11th must be refused."""
    key = "test:burst-trip"
    burst_max = RATE_LIMITS["free"]["burst_max"]
    allowed = sum(1 for _ in range(burst_max + 5)
                  if check_rate_limit(key, "free")["allowed"])
    assert allowed <= burst_max, f"allowed {allowed}, burst_max {burst_max}"


def test_rate_limit_reports_reset_hint():
    key = "test:reset-hint"
    for _ in range(RATE_LIMITS["free"]["burst_max"] + 3):
        result = check_rate_limit(key, "free")
    assert result["allowed"] is False
    assert result["reset_in"] >= 1


def test_enterprise_tier_has_higher_ceiling():
    assert RATE_LIMITS["enterprise"]["burst_max"] > RATE_LIMITS["free"]["burst_max"]
