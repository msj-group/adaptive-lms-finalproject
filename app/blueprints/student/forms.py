"""Native final Assignment forms: teacher-configured text or one private file.

The Student cannot switch the Assignment's type or expand its lifecycle.
"""

from app.i18n import LocalizedFlaskForm as FlaskForm
from wtforms import SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length
from flask import request
from flask_wtf.file import FileField, FileRequired
from wtforms.validators import ValidationError

from app.models import ANSWER_MAX_LENGTH


class SubmissionForm(FlaskForm):
    """One Student's final plain-text answer to one Assignment.

    The form carries **only** ``answer_text``. There is deliberately no
    Student, Group, Assignment, timestamp or status field: every one of
    those is derived server-side -- the Student from the authenticated
    session, the Assignment and Group from the authorized nested public
    identifiers in the URL, ``submitted_at`` from the request's post-lock
    authoritative moment -- so a forged field in the POST body has
    nowhere to land. The signed submission-context token travels as its
    own hidden input rather than as a form field, exactly like the
    Teacher edit snapshot.

    **Validation contract**

    - ``DataRequired`` rejects an empty answer *and* a whitespace-only
      one: WTForms treats a string that is falsy after ``.strip()`` as
      missing, so "   " and "\\n\\n" never reach the database.
    - ``Length(max=ANSWER_MAX_LENGTH)`` is the finite boundary that
      protects the request; ``submissions.answer_text`` is an unbounded
      ``Text`` column. The limit is applied to the **raw** submitted
      value, before normalization, so trimming can never be used to slip
      a longer body past it.

    **Normalization contract** -- :meth:`normalized_answer`

    Leading and trailing whitespace is removed (a plain ``.strip()``, the
    same treatment ``AssignmentForm`` gives ``title`` and
    ``instructions``). Nothing else is touched: internal line breaks,
    internal blank lines, indentation and a browser's ``\\r\\n`` pairs are
    stored exactly as submitted, because they are the Student's own
    formatting of their answer. The value is plain text throughout --
    never HTML, never Markdown, never rendered with ``|safe``.
    """

    #: The finite input boundary for the unbounded ``answer_text`` Text
    #: column. Imported from the model so the two cannot drift.
    ANSWER_MAX = ANSWER_MAX_LENGTH

    answer_text = TextAreaField(
        "Your answer", validators=[DataRequired(), Length(max=ANSWER_MAX_LENGTH)]
    )
    submit = SubmitField("Submit Final Answer")

    def normalized_answer(self):
        """The exact string that will be persisted -- see the class
        docstring's normalization contract."""
        return (self.answer_text.data or "").strip()


class FileSubmissionForm(FlaskForm):
    """One final private file; no comment, drafts or replacement lifecycle."""
    file = FileField("Your file", validators=[FileRequired()])
    submit = SubmitField("Submit Final File")

    def validate_file(self, field):
        if len(request.files.getlist("file")) != 1:
            raise ValidationError("Select exactly one file.")
