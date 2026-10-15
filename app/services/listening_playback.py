"""Finite authored playback choices; normal speed is always available."""
PLAYBACK_SPEEDS = (("0.75", 2), ("1", 1), ("1.25", 4), ("1.5", 8))
DEFAULT_SPEED_MASK = 15
VALID_SPEED_MASKS = tuple(range(1, 16, 2))
SPEED_MASK_CHECK = "playback_speed_mask IN (1, 3, 5, 7, 9, 11, 13, 15)"


def playback_rates(mask):
    if type(mask) is not int or mask not in VALID_SPEED_MASKS:
        raise ValueError("Invalid playback speed configuration")
    return [rate for rate, flag in PLAYBACK_SPEEDS if mask & flag]


def playback_mask(rates):
    values = set(rates)
    if not values.issubset({rate for rate, unused in PLAYBACK_SPEEDS}):
        raise ValueError("Invalid playback speed selection")
    return 1 | sum(flag for rate, flag in PLAYBACK_SPEEDS if rate != "1" and rate in values)
