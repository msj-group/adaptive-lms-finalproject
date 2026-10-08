from flask import current_app
from app.i18n import LocalizedFlaskForm as FlaskForm
from wtforms.fields import DateTimeLocalField
from app.services.schedule_occurrences import from_app_local, LocalTimeError
from wtforms import (
    DateField,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
    TimeField,
)
from wtforms.validators import (
    DataRequired,
    Email,
    InputRequired,
    Length,
    NumberRange,
    Optional,
    ValidationError,
)

from app.extensions import db
from app.security.passwords import ACCOUNT_PASSWORD_MAX_LENGTH, ACCOUNT_PASSWORD_MIN_LENGTH
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    Room,
    Schedule,
    User,
    UserRole,
    UserStatus,
)
from app.services.group_memberships import (
    active_student_enrollment_count,
    conflicting_active_enrollment,
    eligible_active_teacher_count,
)
from app.services.schedule_queries import (
    conflicting_active_schedule,
    range_contains_weekday,
    term_contains_range,
)

# Recurring-schedule weekday convention: Monday=0 .. Sunday=6, matching
# datetime.date.weekday(). Documented in docs/DECISIONS.md (M08).
WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
WEEKDAY_CHOICES = list(enumerate(WEEKDAY_NAMES))

# Minimum/maximum length for administrator-set passwords on any
# administrator-managed user account (currently Student and Teacher). No
# composition rules (uppercase/digit/symbol) are enforced -- strength comes
# from length instead, so long passphrases are preferred over short,
# complex-looking passwords. This follows only the length-over-composition
# principle discussed in guidance such as NIST SP 800-63B; it is not a
# claim of full compliance with that document. In particular, this system
# authenticates with a password as the sole factor (no MFA), which is why
# the minimum sits above the more common 12-character baseline, and
# blocklisting compromised/common passwords is not implemented -- both are
# documented here as a deliberate scope decision, not an oversight. The
# maximum bounds the input size reaching Argon2id, since hashing cost
# scales with input length.
# The shared constants above keep administrator and self-service policy aligned.


class AcademicTermForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=100)])
    start_date = DateField("Start Date", validators=[DataRequired()], render_kw={"type": "date"})
    end_date = DateField("End Date", validators=[DataRequired()], render_kw={"type": "date"})
    submit = SubmitField("Save")

    def __init__(self, *args, term_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._term_id = term_id

    def validate_name(self, field):
        query = AcademicTerm.query.filter(AcademicTerm.name == field.data.strip())
        if self._term_id is not None:
            query = query.filter(AcademicTerm.id != self._term_id)
        if query.first() is not None:
            raise ValidationError("An academic term with this name already exists.")

    def validate_end_date(self, field):
        if self.start_date.data and field.data and field.data <= self.start_date.data:
            raise ValidationError("End date must be after the start date.")


class LevelForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=100)])
    code = StringField("Code", validators=[Optional(), Length(max=20)])
    submit = SubmitField("Save")

    def __init__(self, *args, level_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._level_id = level_id

    def validate_name(self, field):
        query = Level.query.filter(Level.name == field.data.strip())
        if self._level_id is not None:
            query = query.filter(Level.id != self._level_id)
        if query.first() is not None:
            raise ValidationError("A level with this name already exists.")

    def validate_code(self, field):
        if not field.data or not field.data.strip():
            return
        query = Level.query.filter(Level.code == field.data.strip())
        if self._level_id is not None:
            query = query.filter(Level.id != self._level_id)
        if query.first() is not None:
            raise ValidationError("A level with this code already exists.")


class CourseForm(FlaskForm):
    # `validate_choice=False`: the rendered <select> is filtered to active
    # Levels for usability (Part M07C3), but the authoritative "level must
    # be active" decision is made server-side after locking. A forged
    # level_id therefore reaches `validate_level_id` (existence) and then
    # the route's post-lock guard, rather than being silently bounced by
    # WTForms' own choice-matching.
    level_id = SelectField("Level", coerce=int, validators=[DataRequired()], validate_choice=False)
    title = StringField("Title", validators=[DataRequired(), Length(max=150)])
    code = StringField("Code", validators=[Optional(), Length(max=20)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=5000)])
    price = StringField("Course price (LYD)", validators=[InputRequired(), Length(max=32)])
    submit = SubmitField("Save")

    def __init__(self, *args, course_id=None, current_level_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._course_id = course_id
        self.level_id.choices = [
            (
                level.id,
                level.name if level.status == AcademicStatus.ACTIVE.value else f"{level.name} (Archived)",
            )
            for level in Level.query.order_by(Level.display_order, Level.id).all()
            if level.status == AcademicStatus.ACTIVE.value or level.id == current_level_id
        ]

    def validate_level_id(self, field):
        if db.session.get(Level, field.data) is None:
            raise ValidationError("Selected level does not exist.")

    def validate_price(self, field):
        from app.services.money import validate_course_price
        try:
            validate_course_price(field.data)
        except ValueError as error:
            raise ValidationError(str(error)) from None

    def validate_title(self, field):
        query = Course.query.filter(
            Course.level_id == self.level_id.data, Course.title == field.data.strip()
        )
        if self._course_id is not None:
            query = query.filter(Course.id != self._course_id)
        if query.first() is not None:
            raise ValidationError("A course with this title already exists in the selected level.")

    def validate_code(self, field):
        if not field.data or not field.data.strip():
            return
        query = Course.query.filter(
            Course.level_id == self.level_id.data, Course.code == field.data.strip()
        )
        if self._course_id is not None:
            query = query.filter(Course.id != self._course_id)
        if query.first() is not None:
            raise ValidationError("A course with this code already exists in the selected level.")


class GroupForm(FlaskForm):
    # No `status` field: a Group's lifecycle status is owned solely by the
    # dedicated `group_toggle_status` route (Part M07C2). New Groups are
    # created active server-side; `group_edit` never reads or writes
    # status. Do not reintroduce a status control (or a hidden field) here
    # -- it would put a client-controlled value back on the create/edit
    # path the toggle route is meant to be the single owner of.
    # `validate_choice=False` on both ancestor selects: the rendered
    # <select>s are filtered to active AcademicTerms / active Courses
    # under active Levels for usability (Part M07C3), but the
    # authoritative "all three ancestors must be active" decision is made
    # server-side after locking AcademicTerm -> Level -> Course. A forged
    # id reaches `validate_*` (existence) and then the route guard.
    academic_term_id = SelectField(
        "Academic Term", coerce=int, validators=[DataRequired()], validate_choice=False
    )
    course_id = SelectField("Course", coerce=int, validators=[DataRequired()], validate_choice=False)
    name = StringField("Group Name", validators=[DataRequired(), Length(max=100)])
    code = StringField("Group Code", validators=[Optional(), Length(max=20)])
    capacity = IntegerField("Capacity", validators=[DataRequired(), NumberRange(min=1)])
    study_starts_at = DateTimeLocalField("Study starts (center local time)", format="%Y-%m-%dT%H:%M", validators=[InputRequired()])
    submit = SubmitField("Save")

    def __init__(self, *args, group_id=None, current_academic_term_id=None, current_course_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._group_id = group_id
        self.academic_term_id.choices = [
            (
                term.id,
                term.name if term.status == AcademicStatus.ACTIVE.value else f"{term.name} (Archived)",
            )
            for term in AcademicTerm.query.order_by(AcademicTerm.start_date.desc()).all()
            if term.status == AcademicStatus.ACTIVE.value or term.id == current_academic_term_id
        ]
        self.course_id.choices = [
            (
                course.id,
                f"{course.title} ({course.level.name})"
                if course.status == AcademicStatus.ACTIVE.value
                and course.level.status == AcademicStatus.ACTIVE.value
                else f"{course.title} ({course.level.name}) (Archived)",
            )
            for course in Course.query.join(Level).order_by(Level.display_order, Course.display_order).all()
            if (
                course.status == AcademicStatus.ACTIVE.value
                and course.level.status == AcademicStatus.ACTIVE.value
            )
            or course.id == current_course_id
        ]

    def validate_academic_term_id(self, field):
        if db.session.get(AcademicTerm, field.data) is None:
            raise ValidationError("Selected academic term does not exist.")

    def validate_study_starts_at(self, field):
        try:
            self.study_starts_at_utc = from_app_local(current_app.config["APP_TIMEZONE"], field.data)
        except LocalTimeError as exc:
            raise ValidationError(str(exc)) from exc

    def validate_course_id(self, field):
        if db.session.get(Course, field.data) is None:
            raise ValidationError("Selected course does not exist.")

    def validate_name(self, field):
        query = Group.query.filter(
            Group.academic_term_id == self.academic_term_id.data,
            Group.course_id == self.course_id.data,
            Group.name == field.data.strip(),
        )
        if self._group_id is not None:
            query = query.filter(Group.id != self._group_id)
        if query.first() is not None:
            raise ValidationError(
                "A group with this name already exists for the selected term and course."
            )

    def validate_code(self, field):
        if not field.data or not field.data.strip():
            return
        query = Group.query.filter(
            Group.academic_term_id == self.academic_term_id.data,
            Group.course_id == self.course_id.data,
            Group.code == field.data.strip(),
        )
        if self._group_id is not None:
            query = query.filter(Group.id != self._group_id)
        if query.first() is not None:
            raise ValidationError(
                "A group with this code already exists for the selected term and course."
            )


class GroupEnrollmentForm(FlaskForm):
    """Administrator-only enrollment of a Student into one specific Group.

    Deliberately not a subclass of the account-management form bases
    below -- this is a different domain (an association between two
    already-existing records, not an account with a name/email/password).

    Mirrors GroupTeacherAssignmentForm's design: the submitted identifier
    is the Student's `public_id`, never an internal numeric id; the
    target Group is not a form field (it comes from the URL route) but is
    accepted as a constructor keyword so this validator can check
    Group-specific rules (capacity, eligible Teacher, duplicate pair,
    cross-Group conflict); `validate_choice` is off so a tampered
    submission reaches `validate_student_public_id` below instead of
    being silently rejected by WTForms' own choice-matching -- the
    server-side re-validation must not depend on the <select> options
    alone.
    """

    student_public_id = SelectField(
        "Student", coerce=str, validators=[DataRequired()], validate_choice=False
    )
    submit = SubmitField("Enroll Student")

    def __init__(self, *args, group=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._group = group
        existing_student_ids = set()
        if group is not None:
            existing_student_ids = {
                row[0]
                for row in db.session.query(Enrollment.student_id).filter_by(group_id=group.id).all()
            }
        self.student_public_id.choices = [
            (student.public_id, f"{student.full_name} — {student.email}")
            for student in User.query.filter(
                User.role == UserRole.STUDENT.value,
                User.status == UserStatus.ACTIVE.value,
            )
            .order_by(User.full_name, User.id)
            .all()
            if student.id not in existing_student_ids
        ]

    def validate_student_public_id(self, field):
        student = User.query.filter_by(public_id=field.data).first()
        if student is None or student.role != UserRole.STUDENT.value:
            raise ValidationError("Selected student does not exist.")
        if student.status != UserStatus.ACTIVE.value:
            raise ValidationError("Selected student's account is not active.")

        if self._group is None:
            return
        group = self._group

        if group.status != AcademicStatus.ACTIVE.value:
            raise ValidationError("Selected group is not active.")

        if eligible_active_teacher_count(group.id) == 0:
            raise ValidationError(
                "This group must have at least one active teacher before students can be enrolled."
            )

        existing = Enrollment.query.filter_by(student_id=student.id, group_id=group.id).first()
        if existing is not None:
            if existing.status == EnrollmentStatus.ACTIVE.value:
                raise ValidationError("Student is already enrolled in this group.")
            raise ValidationError(
                "A withdrawn enrollment already exists for this student and group. "
                "Reactivate it instead of creating a new one."
            )

        conflict = conflicting_active_enrollment(student.id, group.id)
        if conflict is not None:
            raise ValidationError(
                f"Student is already actively enrolled in Group '{conflict.group.name}' for the same "
                "Course and Academic Term. Withdraw that enrollment first."
            )

        if active_student_enrollment_count(group.id) >= group.capacity:
            raise ValidationError("This group is at full capacity.")


class GroupTeacherAssignmentForm(FlaskForm):
    """Administrator-only assignment of a Teacher to a Group.

    The submitted identifier is the Teacher's `public_id`, not an internal
    numeric id, matching the project's public_id-only convention for
    anything that crosses a request boundary. `validate_choice` is turned
    off so a tampered submission (a Student/Administrator/Researcher
    public_id, a suspended Teacher's public_id, or a public_id that never
    appeared in the rendered choices) reaches `validate_teacher_public_id`
    below instead of being silently rejected by WTForms' own
    choice-matching -- the server-side re-validation must not depend on
    the <select> options alone.

    The target Group is not a form field (it comes from the URL route),
    but is accepted as a constructor keyword so this validator can also
    reject a duplicate/removed assignment for that specific Group, the
    same way GroupEnrollmentForm.validate_student_public_id does for
    Enrollment.
    """

    teacher_public_id = SelectField(
        "Teacher", coerce=str, validators=[DataRequired()], validate_choice=False
    )
    submit = SubmitField("Assign Teacher")

    def __init__(self, *args, group=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._group = group
        # A Teacher who already has ANY assignment row for this Group
        # (active or removed) is excluded from the "assign" choices --
        # an active one has nothing to gain from a second assignment,
        # and a removed one must come back through Reactivate so the
        # same row (and its history) is reused instead of a duplicate.
        existing_teacher_ids = set()
        if group is not None:
            existing_teacher_ids = {
                row[0]
                for row in db.session.query(GroupTeacherAssignment.teacher_id)
                .filter_by(group_id=group.id)
                .all()
            }
        self.teacher_public_id.choices = [
            (teacher.public_id, f"{teacher.full_name} — {teacher.email}")
            for teacher in User.query.filter(
                User.role == UserRole.TEACHER.value,
                User.status == UserStatus.ACTIVE.value,
            )
            .order_by(User.full_name, User.id)
            .all()
            if teacher.id not in existing_teacher_ids
        ]

    def validate_teacher_public_id(self, field):
        teacher = User.query.filter_by(public_id=field.data).first()
        if teacher is None or teacher.role != UserRole.TEACHER.value:
            raise ValidationError("Selected teacher does not exist.")
        if teacher.status != UserStatus.ACTIVE.value:
            raise ValidationError("Selected teacher's account is not active.")

        if self._group is not None:
            existing = GroupTeacherAssignment.query.filter_by(
                group_id=self._group.id, teacher_id=teacher.id
            ).first()
            if existing is not None:
                if existing.status == GroupTeacherAssignmentStatus.ACTIVE.value:
                    raise ValidationError("This teacher is already assigned to this group.")
                raise ValidationError(
                    "A removed assignment already exists for this teacher and group. "
                    "Reactivate it instead of creating a new one."
                )


class ScheduleForm(FlaskForm):
    """Administrator create/edit of one recurring weekly Group schedule
    slot (M08).

    No ``status`` field: a Schedule's active/archived status is owned
    solely by the dedicated ``group_schedule_toggle_status`` route -- the
    create/edit path never reads or writes it. Do not reintroduce a
    status control (or a hidden field).

    The target Group is not a form field (it comes from the URL route)
    but is accepted as a constructor keyword so these validators can run
    the friendly pre-lock checks -- weekday-in-range, time order,
    effective-date order, at-least-one-weekday-occurrence, Academic Term
    containment, exact duplicate, and the active-overlap rule. Every one
    of those is re-checked authoritatively in the route after the
    ``AcademicTerm -> Level -> Course -> Group -> Schedule`` lock chain;
    nothing here is trusted for the final decision.
    """

    day_of_week = SelectField(
        "Day of Week", coerce=int, choices=WEEKDAY_CHOICES, validators=[InputRequired()]
    )
    start_time = TimeField("Start Time", validators=[DataRequired()], render_kw={"type": "time"})
    end_time = TimeField("End Time", validators=[DataRequired()], render_kw={"type": "time"})
    effective_start_date = DateField(
        "Effective Start Date", validators=[DataRequired()], render_kw={"type": "date"}
    )
    effective_end_date = DateField(
        "Effective End Date", validators=[DataRequired()], render_kw={"type": "date"}
    )
    location = StringField("Location", validators=[Optional(), Length(max=255)])
    room_id = SelectField("Room", coerce=int, validators=[Optional()], validate_choice=False)
    submit = SubmitField("Save Schedule")

    def __init__(self, *args, group=None, schedule_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._group = group
        self._schedule_id = schedule_id
        current_room = kwargs.get("obj").room_id if kwargs.get("obj") is not None else None
        self.room_id.choices = [(0, "No room assigned")] + [
            (room.id, room.name + (" (Archived)" if room.status != "active" else ""))
            for room in Room.query.order_by(Room.name, Room.id).all()
            if room.status == "active" or room.id == current_room
        ]

    def validate_room_id(self, field):
        if field.data:
            room = db.session.get(Room, field.data)
            if room is None or room.status != "active":
                raise ValidationError("Select an active room.")

    def validate_day_of_week(self, field):
        if field.data is None or field.data < 0 or field.data > 6:
            raise ValidationError("Select a valid day of the week.")

    def validate_end_time(self, field):
        if self.start_time.data and field.data and field.data <= self.start_time.data:
            raise ValidationError(
                "End time must be after the start time. Overnight slots are not supported."
            )

    def validate_effective_end_date(self, field):
        start = self.effective_start_date.data
        end = field.data
        if start and end and end < start:
            raise ValidationError("Effective end date cannot be before the effective start date.")

        # Composite friendly checks -- only once every field they depend
        # on is present and individually valid.
        if not (
            self.day_of_week.data is not None
            and self.start_time.data
            and self.end_time.data
            and self.end_time.data > self.start_time.data
            and start
            and end
            and end >= start
        ):
            return

        day = self.day_of_week.data
        if not range_contains_weekday(day, start, end):
            raise ValidationError(
                f"The effective date range contains no {WEEKDAY_NAMES[day]}. "
                "Widen the range or pick another day."
            )

        if self._group is None:
            return
        term = self._group.academic_term
        if term is not None and not term_contains_range(
            term.start_date, term.end_date, start, end
        ):
            raise ValidationError(
                "The effective date range must fall within this group's academic term "
                f"({term.start_date.isoformat()} to {term.end_date.isoformat()})."
            )

        duplicate = Schedule.query.filter_by(
            group_id=self._group.id,
            day_of_week=day,
            start_time=self.start_time.data,
            end_time=self.end_time.data,
            effective_start_date=start,
            effective_end_date=end,
        )
        if self._schedule_id is not None:
            duplicate = duplicate.filter(Schedule.id != self._schedule_id)
        if duplicate.first() is not None:
            raise ValidationError("An identical schedule slot already exists for this group.")

        conflict = conflicting_active_schedule(
            self._group.id,
            day,
            self.start_time.data,
            self.end_time.data,
            start,
            end,
            exclude_schedule_id=self._schedule_id,
        )
        if conflict is not None:
            raise ValidationError(
                f"This slot overlaps an existing active {WEEKDAY_NAMES[day]} schedule "
                f"({conflict.start_time.strftime('%H:%M')}-{conflict.end_time.strftime('%H:%M')}, "
                f"effective {conflict.effective_start_date.isoformat()} to "
                f"{conflict.effective_end_date.isoformat()}). Adjust the time or effective dates."
            )


def _normalize_email(raw_email):
    return raw_email.strip().lower() if raw_email else ""


class _AccountCreateFormBase(FlaskForm):
    """Shared fields/validation for admin-created accounts (Student,
    Teacher, ...). Each role gets its own thin public subclass below --
    purely for a role-specific submit label and a clear, discoverable
    class name in routes/templates, not a semantic "is-a" relationship
    between roles.
    """

    full_name = StringField("Full Name", validators=[DataRequired(), Length(max=255)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    password = PasswordField(
        "Temporary Password",
        validators=[DataRequired(), Length(min=ACCOUNT_PASSWORD_MIN_LENGTH, max=ACCOUNT_PASSWORD_MAX_LENGTH)],
        render_kw={"autocomplete": "new-password"},
    )
    confirm_password = PasswordField(
        "Confirm Temporary Password",
        validators=[DataRequired()],
        render_kw={"autocomplete": "new-password"},
    )

    def validate_email(self, field):
        normalized = _normalize_email(field.data)
        if User.query.filter(User.email == normalized).first() is not None:
            raise ValidationError("A user with this email already exists.")

    def validate_confirm_password(self, field):
        if field.data != self.password.data:
            raise ValidationError("Passwords do not match.")


class StudentCreateForm(_AccountCreateFormBase):
    submit = SubmitField("Create Student")


class TeacherCreateForm(_AccountCreateFormBase):
    submit = SubmitField("Create Teacher")


class _AccountEditFormBase(FlaskForm):
    """Shared fields/validation for editing an admin-managed account's
    name/email. Subclasses accept a role-specific `..._id` constructor
    keyword (preserving each role's existing call signature) and forward
    it to the shared exclude-self uniqueness check.
    """

    full_name = StringField("Full Name", validators=[DataRequired(), Length(max=255)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    submit = SubmitField("Save")

    def _set_excluded_user_id(self, excluded_user_id):
        self._excluded_user_id = excluded_user_id

    def validate_email(self, field):
        normalized = _normalize_email(field.data)
        query = User.query.filter(User.email == normalized)
        if self._excluded_user_id is not None:
            query = query.filter(User.id != self._excluded_user_id)
        if query.first() is not None:
            raise ValidationError("A user with this email already exists.")


class StudentEditForm(_AccountEditFormBase):
    def __init__(self, *args, student_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._set_excluded_user_id(student_id)


class TeacherEditForm(_AccountEditFormBase):
    def __init__(self, *args, teacher_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._set_excluded_user_id(teacher_id)


class _AccountPasswordResetFormBase(FlaskForm):
    password = PasswordField(
        "New Temporary Password",
        validators=[DataRequired(), Length(min=ACCOUNT_PASSWORD_MIN_LENGTH, max=ACCOUNT_PASSWORD_MAX_LENGTH)],
        render_kw={"autocomplete": "new-password"},
    )
    confirm_password = PasswordField(
        "Confirm New Temporary Password",
        validators=[DataRequired()],
        render_kw={"autocomplete": "new-password"},
    )
    submit = SubmitField("Reset Password")

    def validate_confirm_password(self, field):
        if field.data != self.password.data:
            raise ValidationError("Passwords do not match.")


class StudentPasswordResetForm(_AccountPasswordResetFormBase):
    pass


class TeacherPasswordResetForm(_AccountPasswordResetFormBase):
    pass
