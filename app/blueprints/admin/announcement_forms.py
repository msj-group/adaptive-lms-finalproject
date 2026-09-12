"""Forms and input parsing for the Administrator Announcement surface
(Phase 4 / M09).

One WTForms form. Unlike the Teacher's -- which has no scope field at all,
because a Teacher may only ever write a ``group`` announcement to the
Group in the URL -- an Administrator genuinely chooses between the three
approved scopes, so the scope and its target are real inputs here.

They are, however, **closed inputs**:

- ``scope`` is a ``SelectField`` whose choices are exactly the three
  :class:`~app.models.enums.AnnouncementScope` members, with WTForms'
  own pre-validation refusing anything else;
- the Course and Group selects are populated from the center's own rows
  and carry **public ids**, never internal ids, so a submitted value is
  matched against a list of real objects and then resolved again by a
  public-id lookup in the write path;
- exactly one target is required, and it is the one the chosen scope
  names. A ``center`` announcement with a Course selected, a ``course``
  announcement with no Course, and a ``group`` announcement carrying a
  Course are all rejected **here**, before any lock -- and the database's
  ``ck_announcements_scope_target`` refuses the same shapes as the final
  defense.

There is no status field, no ``published_at`` field and no author field:
publication is a separate POST-only route behind its own confirmation and
its own signed token, and the author is always the acting Administrator.

Nothing here authorizes anything or touches a lock. Normalisation is not
duplicated either: both text fields go through
``app/services/announcement_text.py``, the same functions the Teacher form
calls. This module owns only the Administrator's wording for each rule.
"""

from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import ValidationError

from app.models import (
    ANNOUNCEMENT_BODY_MAX_LENGTH,
    ANNOUNCEMENT_TITLE_MAX_LENGTH,
    AnnouncementScope,
)
from app.services.announcement_text import (
    CONTROL,
    MISSING,
    TOO_LONG,
    normalize_body,
    normalize_title,
)

_CENTER = AnnouncementScope.CENTER.value
_COURSE = AnnouncementScope.COURSE.value
_GROUP = AnnouncementScope.GROUP.value

#: The three scopes, in the order an Administrator thinks about them --
#: widest first. Declared once so the form, the filter and the badges
#: cannot disagree about the wording.
SCOPE_CHOICES = (
    (_CENTER, "Center — everybody at the center"),
    (_COURSE, "Course — everybody currently in one course"),
    (_GROUP, "Group — one group's current members"),
)

TITLE_MESSAGES = {
    MISSING: "Give this announcement a title.",
    CONTROL: "The title may only contain ordinary text.",
    TOO_LONG: f"The title must be at most {ANNOUNCEMENT_TITLE_MAX_LENGTH} characters.",
}
BODY_MESSAGES = {
    MISSING: "Write the announcement.",
    CONTROL: "The announcement may only contain ordinary text and line breaks.",
    TOO_LONG: (
        f"The announcement must be at most {ANNOUNCEMENT_BODY_MAX_LENGTH} characters."
    ),
}

_COURSE_REQUIRED = "Choose the course this announcement is for."
_GROUP_REQUIRED = "Choose the group this announcement is for."
_CENTER_HAS_TARGET = (
    "A center announcement is addressed to everybody, so it cannot also name a course "
    "or a group. Clear the selection or choose a narrower scope."
)
_WRONG_TARGET = (
    "This announcement's scope does not match the target you chose. Pick the scope "
    "first, then the course or group it names."
)


class AdminAnnouncementForm(FlaskForm):
    """Scope, target, title and body for one announcement.

    The validated, normalized results are stored on the form
    (:attr:`normalized_title`, :attr:`normalized_body`,
    :attr:`target_public_id`) so the route persists exactly what was
    validated rather than re-deriving it from the raw request a second
    time and risking a different answer.
    """

    scope = SelectField("Who is this for?", validate_choice=True)
    course = SelectField("Course", validate_choice=False)
    group = SelectField("Group", validate_choice=False)
    title = StringField("Title")
    body = TextAreaField("Announcement")
    submit = SubmitField("Save draft")

    def __init__(self, *args, course_choices=(), group_choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.scope.choices = list(SCOPE_CHOICES)
        #: A leading blank so "no target" is expressible and is what an
        #: untouched form submits -- rather than silently defaulting to
        #: whichever Course happens to sort first.
        self.course.choices = [("", "— none —")] + list(course_choices)
        self.group.choices = [("", "— none —")] + list(group_choices)
        self._course_ids = {value for value, _ in course_choices}
        self._group_ids = {value for value, _ in group_choices}
        self.normalized_title = None
        self.normalized_body = None
        self.target_public_id = ""

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

    def validate_scope(self, field):
        """Prove the scope/target combination is one of the three legal
        shapes, and record which public id the write path must resolve.

        Runs on the ``scope`` field rather than on either target field
        because the rule is about the *combination*: a Course select is
        neither required nor forbidden on its own, only relative to the
        scope beside it.
        """
        scope = field.data
        course = (self.course.data or "").strip()
        group = (self.group.data or "").strip()

        if scope == _CENTER:
            if course or group:
                raise ValidationError(_CENTER_HAS_TARGET)
            self.target_public_id = ""
            return
        if scope == _COURSE:
            if group:
                raise ValidationError(_WRONG_TARGET)
            if not course:
                raise ValidationError(_COURSE_REQUIRED)
            if course not in self._course_ids:
                raise ValidationError(_COURSE_REQUIRED)
            self.target_public_id = course
            return
        if scope == _GROUP:
            if course:
                raise ValidationError(_WRONG_TARGET)
            if not group:
                raise ValidationError(_GROUP_REQUIRED)
            if group not in self._group_ids:
                raise ValidationError(_GROUP_REQUIRED)
            self.target_public_id = group
            return
        raise ValidationError(_WRONG_TARGET)  # pragma: no cover -- SelectField caught it
