"""Teacher forms for Group-owned Speaking activities (Phase 4 / M06).

Kept in its own module rather than appended to
``app/blueprints/teacher/forms.py`` so the Speaking surface stays readable
on its own and the existing M01/M03/M12 forms are not enlarged. No
existing form is changed.

:class:`SpeakingActivityForm` is the M01 :class:`AssignmentForm` --
**subclassed, not copied** -- because a Speaking activity *is* an
Assignment: the same four editable fields, the same local-wall-clock
``datetime-local`` parsing through the shared M09 timezone policy, and the
same ``opens_at < due_at`` rule enforced against the converted UTC values.
Restating any of that here would have created a second definition that
could drift from the one the database CHECK actually constrains. Only two
things differ, and both are wording: the duplicate-title message (the
``uq_assignments_group_title`` constraint spans **both** kinds, so the
message must name both, rather than reporting a clash against something
the Teacher cannot see on this page) and the submit button.

:class:`SpeakingFeedbackForm` is M03's
:class:`~app.blueprints.teacher.forms.SubmissionFeedbackForm` contract
applied to a recording: one plain-text field, no score, no grade, no
rubric, no pass/fail, no publish control.
"""

from app.i18n import LocalizedFlaskForm as FlaskForm
from wtforms import SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, ValidationError

from app.blueprints.teacher.forms import AssignmentForm
from app.models import SPEAKING_FEEDBACK_MAX_LENGTH, Assignment


class SpeakingActivityForm(AssignmentForm):
    """Teacher create/edit of one Group-owned Speaking activity.

    **What this form deliberately cannot carry.** There is no
    ``group_id``, ``assignment_id``, ``speaking_id``, ``status``,
    ``published_at``, ``version``, ``public_id``, ``creation_nonce``,
    audio, submission, feedback, score or grade field. The Group and the
    activity are the authorized public identifiers in the URL,
    publication belongs solely to its own POST routes, and every id and
    timestamp is server-owned -- so a forged field of any of those names
    has **nowhere to land**. The signed stale-form token travels as its
    own hidden input rather than as a form field, exactly like the M01
    assignment snapshot and the M03 feedback state.

    **There is no file field here either, and there cannot be.** The
    Teacher authors the task; the *Student* records the answer. A Speaking
    activity carries no audio of its own -- that is the whole difference
    from an M05 Listening activity.
    """

    submit = SubmitField("Save Speaking Activity")

    def validate_title(self, field):
        """The friendly pre-lock duplicate-title guard.

        ``uq_assignments_group_title`` is the final defense and it spans
        the whole ``assignments`` table, so a Speaking activity and an
        ordinary Assignment in one Group cannot share a title either --
        which this message says plainly. The authoritative recheck runs
        against the **locked** Group in the route.
        """
        if self._group_id is None:
            return
        title = (field.data or "").strip()
        if not title:
            # A value `DataRequired` has already rejected -- a second
            # error on the same field would only be noise.
            return
        query = Assignment.query.filter(
            Assignment.group_id == self._group_id, Assignment.title == title
        )
        if self._assignment_id is not None:
            query = query.filter(Assignment.id != self._assignment_id)
        if query.first() is not None:
            raise ValidationError(
                "An assignment or speaking activity with this title already exists in this "
                "group."
            )


class SpeakingFeedbackForm(FlaskForm):
    """One Teacher's plain-text feedback on one immutable Speaking
    recording (Phase 4 / M06).

    The form carries **only** ``feedback_text``. There is deliberately no
    reviewer, submission, activity, Group, version, timestamp or status
    field: the reviewer is the authenticated session, the submission /
    activity / Group are the authorized nested public identifiers in the
    URL, ``version`` is derived from the locked row, and both timestamps
    are the request's post-lock authoritative moment -- so a forged field
    in the POST body has nowhere to land. The signed stale-form token
    travels as its own hidden input rather than as a form field.

    There is likewise **no** score, grade, rubric, pronunciation scale,
    fluency scale, pass/fail or publish control: saving feedback is not
    grading, and it has no draft state to publish out of.

    **Validation contract**

    - ``DataRequired`` rejects empty *and* whitespace-only text: WTForms
      treats a string that is falsy after ``.strip()`` as missing, so
      ``"   "`` and ``"\\n\\n"`` never reach the database.
    - ``Length(max=SPEAKING_FEEDBACK_MAX_LENGTH)`` is the finite boundary
      that protects the request; ``speaking_feedback.feedback_text`` is an
      unbounded ``Text`` column. The limit is applied to the **raw**
      submitted value, before normalization, so trimming can never be used
      to slip a longer body past it.

    **Normalization contract** -- :meth:`normalized_feedback` is a plain
    ``.strip()``. Nothing else is touched: internal line breaks, blank
    lines, indentation and a browser's ``\\r\\n`` pairs are stored exactly
    as typed. The value is plain text throughout -- never HTML, never
    Markdown, never rendered with ``|safe``. The **normalized** value is
    also what decides the no-op.
    """

    #: The finite input boundary for the unbounded ``feedback_text`` Text
    #: column. Imported from the model so the two cannot drift.
    FEEDBACK_MAX = SPEAKING_FEEDBACK_MAX_LENGTH

    feedback_text = TextAreaField(
        "Feedback for this student",
        validators=[DataRequired(), Length(max=SPEAKING_FEEDBACK_MAX_LENGTH)],
    )
    submit = SubmitField("Save Feedback")

    def normalized_feedback(self):
        """The exact string that will be persisted -- see the class
        docstring's normalization contract."""
        return (self.feedback_text.data or "").strip()
