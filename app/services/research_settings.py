"""Deployment settings for research collection, resolved once at start-up
(Phase 6 replacement). Fail closed: an invalid value refuses to start the
application rather than run with a guessed setting.

- ``RESEARCH_DATA_PROVENANCE`` -- ``study`` or ``development``. What a
  session collected by this deployment is labelled; only ``study`` data is
  ever exported. Every environment defaults to ``development``. Running an
  operational review on a production server cannot create study data unless
  an actual study is explicitly configured.
- ``RESEARCH_RETENTION_DAYS`` -- the retention the deployment supplies, as a
  whole number of days (1-3650), or unset. **There is no default**: while it
  is unset no configuration can be activated, so collection cannot start with
  an undocumented indefinite retention.
- ``RESEARCHER_EMAIL_ALLOWLIST`` -- comma-separated addresses the
  provisioning script may create Researcher accounts for. It is consulted
  only by ``scripts/create_researcher.py``; it never grants a role at login.
"""

from app.services.research_scope import DEPLOYMENT_PROVENANCES

MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 3650


class ResearchSettingsError(ValueError):
    """An invalid research deployment setting."""


def parse_retention_days(raw):
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise ResearchSettingsError("RESEARCH_RETENTION_DAYS must be a whole number") from exc
    if not MIN_RETENTION_DAYS <= value <= MAX_RETENTION_DAYS:
        raise ResearchSettingsError(
            f"RESEARCH_RETENTION_DAYS must be between {MIN_RETENTION_DAYS} and "
            f"{MAX_RETENTION_DAYS}"
        )
    return value


def parse_allowlist(raw):
    if not raw:
        return frozenset()
    return frozenset(
        part.strip().lower() for part in str(raw).split(",") if part.strip()
    )


def resolve_research_settings(config):
    """Validate and normalise the three settings in place."""
    provenance = (config.get("RESEARCH_DATA_PROVENANCE") or "").strip().lower()
    if provenance not in DEPLOYMENT_PROVENANCES:
        raise ResearchSettingsError(
            "RESEARCH_DATA_PROVENANCE must be one of: " + ", ".join(DEPLOYMENT_PROVENANCES)
        )
    config["RESEARCH_DATA_PROVENANCE"] = provenance
    config["RESEARCH_RETENTION_DAYS"] = parse_retention_days(config.get("RESEARCH_RETENTION_DAYS"))
    config["RESEARCHER_EMAIL_ALLOWLIST"] = parse_allowlist(config.get("RESEARCHER_EMAIL_ALLOWLIST"))
