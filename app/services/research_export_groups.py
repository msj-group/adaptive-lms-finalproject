"""Owner-authorized group selection; no LMS identity or mapping is returned.

This private server query resolves the cohort at the export's read snapshot.
Only a session membership predicate and group choices leave this service.
"""
from app.extensions import db
from app.models import Enrollment, Group, ResearchSession, ResearchSubjectLink


def group_choices():
    return [(row.public_id, row.name) for row in db.session.query(Group.public_id, Group.name)
            .order_by(Group.name, Group.id).limit(201).all()]


def normalize_groups(values):
    if values is None:
        return ()
    if not isinstance(values, (list, tuple)) or len(values) > 200 or any(
            not isinstance(value, str) or len(value) != 36 for value in values):
        raise ValueError("Select valid groups.")
    selected = tuple(sorted(set(values)))
    if selected:
        known = {row[0] for row in db.session.query(Group.public_id).filter(Group.public_id.in_(selected)).all()}
        if set(selected) != known:
            raise ValueError("A selected group is unavailable.")
    return selected


def group_condition(values):
    selected = normalize_groups(values)
    if not selected:
        return ResearchSession.id.is_not(None)
    subject_ids = db.session.query(ResearchSubjectLink.subject_id).join(
        Enrollment, Enrollment.student_id == ResearchSubjectLink.user_id).join(
        Group, Group.id == Enrollment.group_id).filter(
            Group.public_id.in_(selected), Enrollment.status == "active").distinct()
    return ResearchSession.subject_id.in_(subject_ids)
