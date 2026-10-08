"""Teacher forms for Group-owned Listening activities (Phase 4 / M05).

Kept in its own module rather than appended to
``app/blueprints/teacher/quiz_forms.py`` so the Listening surface stays
readable on its own and the M04 quiz forms are not enlarged. Nothing here
is imported by any existing form, and no existing form is changed.

:class:`ListeningContentForm` owns what a Listening activity *says* --
its title, instructions, transcript, transcript policy and vocabulary
notes. :class:`ListeningCreateForm` is that same content plus the one
required recording, which exists only on the create page because the
audio is immutable afterwards.

**Availability, timing and attempt settings are not here.** They are the
Quiz's own settings, and M05 reuses ``quiz_forms.QuizSettingsForm``
unchanged rather than declaring a second, drifting copy of the same four
fields and the same timezone conversion.
"""

from app.i18n import LocalizedFlaskForm as FlaskForm
from flask_wtf.file import FileField, FileRequired
from wtforms import RadioField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

from app.models import (
    QUIZ_INSTRUCTIONS_MAX_LENGTH,
    QUIZ_TITLE_MAX_LENGTH,
    TRANSCRIPT_MAX_LENGTH,
    VOCABULARY_NOTES_MAX_LENGTH,
    TranscriptVisibility,
)
from app.services.quiz_queries import duplicate_title_exists

#: The two audio formats a Listening activity accepts, named once so the
#: form's help text, the Teacher template and the create route cannot
#: describe the supported set differently. They are exactly the two
#: ``audio`` extensions the existing M12 upload pipeline already
#: validates; M05 adds no format and no second storage implementation.
SUPPORTED_AUDIO_EXTENSIONS = ("mp3", "wav")

#: What the file input advertises. A courtesy for the file picker only --
#: the server never trusts it, and ``file_validation`` re-derives the
#: extension, the declared MIME and the binary signature itself.
AUDIO_ACCEPT_ATTRIBUTE = ".mp3,.wav,audio/mpeg,audio/wav"


class _ListeningContentFields(FlaskForm):
    """The authored content every Listening form carries.

    **What these forms deliberately cannot carry.** There is no
    ``group_id``, ``quiz_id``, ``listening_id``, ``audio_file_id``,
    ``status``, ``published_at``, ``opens_at``, ``closes_at``,
    ``time_limit``, ``attempt_limit``, ``version``, ``public_id``,
    ``storage_key``, ``sha256``, question, option, answer-key, score or
    attempt field. The Group and the activity are the authorized public
    identifiers in the URL, the recording is resolved from the locked
    extension row, availability belongs solely to the settings form,
    publication solely to its own POST route, and every id, version and
    timestamp is server-owned -- so a forged field of any of those names
    has **nowhere to land**. The signed stale-form token travels as its
    own hidden input rather than as a form field, exactly like the M01
    assignment snapshot, the M03 feedback state and the M04A quiz edit
    state.

    **Validation contract**

    - ``DataRequired`` rejects empty *and* whitespace-only ``title`` and
      ``instructions``: WTForms treats a string that is falsy after
      ``.strip()`` as missing, so ``"   "`` never reaches the database.
    - ``transcript`` and ``vocabulary_notes`` are genuinely optional. An
      empty transcript is legitimate under **every** transcript policy --
      a Teacher may decide the policy before writing the text, and
      nothing is fabricated to fill the gap.
    - ``Length(max=...)`` is applied by WTForms to the **raw** submitted
      value, before any trimming these classes perform, so padding a body
      with whitespace can never be used to slip a longer one past the
      limit.

    **Normalization contract** -- a plain ``.strip()`` on all four text
    fields, applied *after* the length validators have run against the raw
    value. Nothing else is touched: internal line breaks, blank lines,
    indentation and a browser's ``\\r\\n`` pairs are stored exactly as
    typed. Every value is plain text throughout -- never HTML, never
    Markdown, never rendered with ``|safe``.

    The **normalized** values are also what decide the edit no-op: a save
    whose normalized title, instructions, transcript, policy and
    vocabulary all equal the stored ones changes nothing at all, so
    re-saving unchanged values cannot bump ``Quiz.version`` or move a
    timestamp.

    **The title check here is a friendly pre-lock guard only.** It shares
    its single definition (``quiz_queries.duplicate_title_exists``) with
    the authoritative post-lock recheck, so the two cannot drift, and the
    database ``UniqueConstraint(group_id, title)`` remains the final
    defense. That constraint spans the **whole** ``quizzes`` table, so a
    Listening activity and an ordinary Quiz in one Group cannot share a
    title either -- which the message says plainly rather than reporting a
    clash against something the Teacher cannot see on this page.
    """

    TITLE_MAX = QUIZ_TITLE_MAX_LENGTH
    INSTRUCTIONS_MAX = QUIZ_INSTRUCTIONS_MAX_LENGTH
    TRANSCRIPT_MAX = TRANSCRIPT_MAX_LENGTH
    VOCABULARY_MAX = VOCABULARY_NOTES_MAX_LENGTH

    title = StringField("Title", validators=[DataRequired(), Length(max=TITLE_MAX)])
    instructions = TextAreaField(
        "Instructions", validators=[DataRequired(), Length(max=INSTRUCTIONS_MAX)]
    )
    transcript = TextAreaField(
        "Transcript (optional)", validators=[Optional(), Length(max=TRANSCRIPT_MAX)]
    )
    transcript_visibility = RadioField(
        "Who may read the transcript",
        choices=[
            (TranscriptVisibility.HIDDEN.value, "Hidden from students"),
            (
                TranscriptVisibility.AFTER_SUBMISSION.value,
                "Only after a student finishes an attempt",
            ),
            (TranscriptVisibility.ALWAYS.value, "Available to students at any time"),
        ],
        default=TranscriptVisibility.HIDDEN.value,
        validators=[DataRequired()],
    )
    vocabulary_notes = TextAreaField(
        "Vocabulary support (optional)",
        validators=[Optional(), Length(max=VOCABULARY_MAX)],
    )

    def __init__(self, *args, group_id=None, quiz_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._group_id = group_id
        self._quiz_id = quiz_id

    def normalized_title(self):
        return (self.title.data or "").strip()

    def normalized_instructions(self):
        return (self.instructions.data or "").strip()

    def normalized_transcript(self):
        """The exact transcript that will be persisted. ``None`` becomes
        ``""`` so an unwritten transcript has exactly one spelling in the
        column -- see :class:`~app.models.listening_activity.ListeningActivity`."""
        return (self.transcript.data or "").strip()

    def normalized_vocabulary_notes(self):
        return (self.vocabulary_notes.data or "").strip()

    def validate_title(self, field):
        title = (field.data or "").strip()
        if self._group_id is None or not title:
            # No Group context (nothing to compare within), or a value
            # `DataRequired` has already rejected -- a second error on the
            # same field would only be noise.
            return
        if duplicate_title_exists(self._group_id, title, exclude_quiz_id=self._quiz_id):
            raise ValidationError(
                "A quiz or listening activity with this title already exists in this group."
            )


class ListeningCreateForm(_ListeningContentFields):
    """Create one Listening activity, with its one required recording.

    ``FileRequired`` only confirms that a file part was actually sent. The
    bytes themselves are streamed, size-checked, extension-checked,
    declared-MIME-checked and signature-checked by
    ``app.services.file_validation`` / ``file_storage`` in the route --
    **before** any database lock is taken -- and the server, never the
    browser, decides the stored extension, category and content type.

    The field exists on this form and on no other, because the audio is
    immutable once the create transaction commits: M05 adds no
    replacement route and no hard delete, so a Teacher who attached the
    wrong recording creates another draft.
    """

    audio = FileField(
        "Audio recording (MP3 or WAV)",
        validators=[FileRequired(message="Choose an MP3 or WAV recording to upload.")],
    )
    submit = SubmitField("Create Listening Activity")


class ListeningContentForm(_ListeningContentFields):
    """Edit an existing Listening activity's authored content.

    Deliberately carries **no** file field: the recording cannot be
    replaced, and offering a control that would be refused server-side
    would be a lie about what the page can do. Everything else about the
    contract is inherited unchanged.
    """

    submit = SubmitField("Save Changes")
