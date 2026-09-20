"""Forms and input parsing for the Administrator research area
(Phase 6 / M01).

One WTForms form: a consent document's version identifier, title and body.

**There is no status, digest, activation, creator or participant field, and
no placeholder for one.** A new document is a ``draft``; activation is a
separate POST-only route with its own confirmation and signed token; the
digest is computed on the server from the normalised values that are about
to be stored.

The participant invitation form deliberately has **no** WTForms class: it
posts one Student ``public_id`` and one signed stale-state token, both of
which are re-proved against locked rows, so a form object would only add a
second place for the rules to live.

Nothing here authorizes anything or touches a lock. Every check is a
friendly pre-lock guard; the route re-proves what depends on other rows (a
unique version identifier) against locked rows before writing. This module
owns only the Administrator's wording.
"""

from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, TextAreaField
from wtforms.validators import ValidationError

from app.models import (
    CONSENT_BODY_MAX_LENGTH,
    CONSENT_TITLE_MAX_LENGTH,
    CONSENT_VERSION_MAX_LENGTH,
)
from app.services.research_text import (
    CONTROL,
    MISSING,
    TOO_LONG,
    normalize_consent_body,
    normalize_consent_title,
    normalize_consent_version,
)

VERSION_MESSAGES = {
    MISSING: "Give this consent document a version identifier, for example v1.0.",
    CONTROL: "The version identifier may only contain ordinary text.",
    TOO_LONG: (
        f"The version identifier must be at most {CONSENT_VERSION_MAX_LENGTH} characters."
    ),
}
TITLE_MESSAGES = {
    MISSING: "Give this consent document a title.",
    CONTROL: "The title may only contain ordinary text.",
    TOO_LONG: f"The title must be at most {CONSENT_TITLE_MAX_LENGTH} characters.",
}
BODY_MESSAGES = {
    MISSING: "Enter the approved consent wording students will read.",
    CONTROL: "The consent wording may only contain ordinary text and line breaks.",
    TOO_LONG: f"The consent wording must be at most {CONSENT_BODY_MAX_LENGTH} characters.",
}


def _normalized(normalizer, messages):
    """A WTForms validator that replaces the field's data with its
    normalised value, or raises the matching sentence.

    Normalising here rather than in the route means the value the form
    redisplays after an error is the value that would have been stored --
    and, because the digest is taken over exactly these strings, the value
    the Administrator reviewed is the value that gets hashed.
    """

    def _validate(_form, field):
        value, error = normalizer(field.data)
        if error is not None:
            raise ValidationError(messages[error])
        field.data = value

    return _validate


class ConsentDocumentForm(FlaskForm):
    """A new draft consent document."""

    version_identifier = StringField(
        "Version identifier", validators=[_normalized(normalize_consent_version, VERSION_MESSAGES)]
    )
    title = StringField(
        "Title", validators=[_normalized(normalize_consent_title, TITLE_MESSAGES)]
    )
    body = TextAreaField(
        "Consent wording", validators=[_normalized(normalize_consent_body, BODY_MESSAGES)]
    )
    submit = SubmitField("Save draft")
