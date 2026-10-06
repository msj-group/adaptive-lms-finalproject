"""Reusable SQL scoping for current-episode learning records."""
from sqlalchemy import exists, select
from sqlalchemy.orm import aliased
from app.models.enrollment import Enrollment


def active_episode_record(model):
    episode = aliased(Enrollment)
    return exists(select(episode.id).where(episode.id == model.enrollment_id,
        episode.student_id == model.student_id, episode.status == "active").correlate(model))
