"""Frozen initial v2 pilot policy; revising it requires a reviewed protocol."""
POLICY = {
    "session_inactivity_minutes": 30, "max_prompts_per_session": 2,
    "max_prompts_per_day": 3, "lookback_seconds": 120, "min_observed_seconds": 90,
    "random_prompt_permille": 100, "activity_end_prompt_permille": 0,
    "offer_ttl_seconds": 180, "response_window_seconds": 60,
}
PROTOCOL = {
    "study_timezone": "Africa/Tripoli",
    "protocol_version": "natural-use-protocol.v2", "feature_version": "recent-frustration-features.v1",
    "first_opportunity_seconds": 120, "opportunity_step_seconds": 60,
    "minimum_spacing_seconds": 600, "heartbeat_seconds": 30,
    "maximum_gap_seconds": 45, "maximum_clock_uncertainty_ms": 5000,
}
