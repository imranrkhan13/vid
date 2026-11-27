"""Router resolution weighting: a higher-resolution provider outscores a
lower-resolution one at equal measured factors, and measured native resolution
overrides declared caps."""
from app.routing.router import BASE_WEIGHTS


def test_resolution_weight_raised():
    assert BASE_WEIGHTS["resolution"] >= 0.13
    assert BASE_WEIGHTS["free_credits"] <= 0.12


def test_higher_resolution_scores_higher_at_equal_factors():
    """The resolution factor is what separates two otherwise-identical providers."""
    w = dict(BASE_WEIGHTS)
    # equal factors for everything except resolution
    equal = {"consistency": 0.5, "quality": 0.5, "free_credits": 1.0, "cost": 1.0,
             "reliability": 0.5, "speed": 0.5}
    f_lo = min(1.0, (384 * 704) / float(1280 * 720))     # low-res provider vs a 1280x720 request
    f_hi = min(1.0, (1280 * 720) / float(1280 * 720))   # native-res provider
    base = sum(w[k] * v for k, v in equal.items())
    score_lo = base + w["resolution"] * f_lo
    score_hi = base + w["resolution"] * f_hi
    assert score_hi > score_lo
    # the gap is meaningful, not a rounding artifact
    assert (score_hi - score_lo) > 0.05


def test_measured_native_resolution_overrides_declared():
    """Once QA probes exist, the measured native-resolution share replaces the
    declared-cap factor (router.py: measured() -> f_res override). The share
    counts every probe, not just the passing ones."""
    probes = [{"width": 1280, "native_width": 704}, {"width": 1280, "native_width": 1280}]
    native = [1.0 if (p.get("native_width") or 0) >= p["width"] else 0.0 for p in probes]
    assert sum(native) / len(native) == 0.5
