"""Student form for the one final Speaking recording (Phase 4 / M06).

Kept beside ``app/blueprints/student/forms.py`` rather than inside it so
the M02 text-answer form's accepted contract is untouched.
"""

from app.i18n import LocalizedFlaskForm as FlaskForm
from flask_wtf.file import FileField, FileRequired
from wtforms import SubmitField


class SpeakingSubmissionForm(FlaskForm):
    """One Student's single, final recording for one Speaking activity.

    **The form carries exactly one field: the audio part.** There is
    deliberately no Student, Group, activity, submission, timestamp,
    status, filename, MIME, size, digest, storage-key, attempt or score
    field. The Student is the authenticated session, the activity and
    Group are the authorized nested public identifiers in the URL,
    ``submitted_at`` is the request's post-lock authoritative moment, and
    every id is server-generated -- so a forged field of any of those
    names has **nowhere to land**. The signed submission token travels as
    its own hidden input rather than as a form field, exactly like the M02
    submission context and the M03 feedback state.

    ``FileRequired`` only confirms that a file part was actually sent. The
    bytes themselves are streamed, size-checked, extension-checked,
    declared-MIME-checked and signature-checked by
    ``app.services.speaking_audio`` in the route -- **before** any database
    lock is taken -- and the server, never the browser, decides the stored
    extension, category and content type.

    The same field serves both paths the recording page offers: the
    ``MediaRecorder`` module attaches the recorded Blob to it under a
    safe generated filename derived from the negotiated MIME type, and a
    browser without ``MediaRecorder`` shows it as an ordinary
    ``accept="audio/*"`` file input. The server cannot tell the two apart,
    and deliberately does not try: both are untrusted bytes and both go
    through the identical validation.
    """

    audio = FileField(
        "Your recording",
        validators=[FileRequired(message="Record or choose an audio file first.")],
    )
    submit = SubmitField("Submit Final Recording")
