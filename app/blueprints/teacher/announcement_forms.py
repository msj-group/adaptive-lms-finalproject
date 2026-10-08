"""Forms and input parsing for the Teacher Announcement surface
(Phase 4 / M09).

One WTForms form, because the Teacher's announcement has exactly two
inputs: a title and a body. The **scope is not an input** on this
surface -- a Teacher may only ever write a ``group`` announcement, and
the Group comes from the URL -- so there is no scope field, no target
field and no hidden value carrying either. A forged ``scope=center`` in
the request body has nowhere to land, because nothing reads one.

There is likewise no status field, no ``published_at`` field, no author
field and no "publish immediately" checkbox: publication is a separate
POST-only route behind its own confirmation and its own signed token, so
a forged lifecycle value in this body cannot reach a column.

Nothing here authorizes anything or touches a lock. These are ordinary
input checks that run **before** any lock is taken, so a mistyped notice
costs no database work and the author gets their own text back to
correct. Every authoritative condition -- the Group, the assignment, the
operational chain, the draft state, the stale-state token -- is re-checked
against the **locked** rows afterwards.

Normalisation is not duplicated here: both fields go through
``app/services/announcement_text.py``, the same functions the
Administrator form calls, so the two surfaces cannot drift on what a
title or a body *is*. This module owns only the Teacher's wording for
each rule.
"""

from app.i18n import LocalizedFlaskForm as FlaskForm
from wtforms import StringField, SubmitField, TextAreaField
from wtforms.validators import ValidationError

from app.models import ANNOUNCEMENT_BODY_MAX_LENGTH, ANNOUNCEMENT_TITLE_MAX_LENGTH
from app.services.announcement_text import (
    CONTROL,
    MISSING,
    TOO_LONG,
    normalize_body,
    normalize_title,
)

#: One sentence per rule, per field. Declared here rather than inline so
#: the create form and the edit form cannot word the same rejection two
#: different ways.
TITLE_MESSAGES = {
    MISSING: "Give this announcement a title.",
    CONTROL: "The title may only contain ordinary text.",
    TOO_LONG: (
        f"The title must be at most {ANNOUNCEMENT_TITLE_MAX_LENGTH} characters."
    ),
}
BODY_MESSAGES = {
    MISSING: "Write the announcement.",
    CONTROL: "The announcement may only contain ordinary text and line breaks.",
    TOO_LONG: (
        f"The announcement must be at most {ANNOUNCEMENT_BODY_MAX_LENGTH} characters."
    ),
}


class AnnouncementTextForm(FlaskForm):
    """The title and body of one announcement.

    Both validators store the **normalized** value on the form
    (:attr:`normalized_title` / :attr:`normalized_body`) so the route
    persists exactly what was validated rather than re-normalising the
    raw string a second time and risking a different answer.
    """

    title = StringField("Title")
    body = TextAreaField("Announcement")
    submit = SubmitField("Save draft")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.normalized_title = None
        self.normalized_body = None

    def validate_title(self, field):
        value, error = normalize_title(field.data)
        if error is not None:
            raise ValidationError(TITLE_MESSAGES[error])
        self.normalized_title = value

    def validate_body(self, field):
        value, error = normalize_body(field.data)
        if error is not None:
            raise ValidationError(BODY_MESSAGES[error])
        self.normalized_body = value
