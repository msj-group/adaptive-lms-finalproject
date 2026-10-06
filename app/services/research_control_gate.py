from app.extensions import db
from app.models.research_storage import ResearchConfigurationSequence


def lock_research_control():
    """No reset. Serialize configuration numbering and raw/archive removal."""
    row = ResearchConfigurationSequence.query.filter_by(id=1).populate_existing().with_for_update().first()
    if row is None:
        raise ValueError("Research control schema is not initialized.")
    return row


def lock_all_configurations():
    """Bounded current reads while the caller owns the configuration gate."""
    from app.models import ResearchConfiguration
    last_id, collecting = 0, False
    while True:
        rows = db.session.query(ResearchConfiguration.id, ResearchConfiguration.is_collecting).filter(
            ResearchConfiguration.id > last_id).order_by(ResearchConfiguration.id).limit(500).with_for_update().all()
        if not rows:
            return collecting
        collecting = collecting or any(value for _id, value in rows)
        last_id = rows[-1][0]
