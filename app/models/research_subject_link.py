"""The protected link from a research subject to a Student account
(Phase 6 replacement).

**The only place a research identity meets an LMS account.** Collection
resolves the acting Student's subject through this row (creating both at
the first collection write, under the population rule), and the operator
tool (``scripts/research_operator.py``) uses it to exclude, reinstate or mark
a Student as demonstration data. No Researcher page, Researcher query or
export reads this table, and nothing else joins research data to ``users``.

**A foreign key to ``users`` proves the row exists, never that it is an
active Student.** Every collection write re-proves role and status against
the account itself.

One subject per account and one account per subject, both unique. The row is
immutable and never deleted: removing it would let a later collection write
create a second, unrelated identity for the same Student -- and silently
drop an exclusion.
"""

from sqlalchemy import event

from app.extensions import db
from app.models.research_common import ID_TYPE, ResearchDataError
from app.models.submission_feedback import whole_second_utc


class ResearchSubjectLink(db.Model):
    __tablename__ = "research_subject_links"
    __table_args__ = (
        db.UniqueConstraint("subject_id", name="uq_research_subject_links_subject_id"),
        db.UniqueConstraint("user_id", name="uq_research_subject_links_user_id"),
    )

    id = db.Column(ID_TYPE, primary_key=True)
    subject_id = db.Column(ID_TYPE, db.ForeignKey("research_subjects.id"), nullable=False)
    user_id = db.Column(ID_TYPE, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)


@event.listens_for(ResearchSubjectLink, "before_update")
def _account_link_is_immutable(_mapper, _connection, _target):
    raise ResearchDataError("A research subject's account link never changes")


@event.listens_for(ResearchSubjectLink, "before_delete")
def _refuse_deleting_an_account_link(_mapper, _connection, _target):
    raise ResearchDataError("A research subject's account link is never deleted")
