import enum


class AcademicStatus(str, enum.Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class LessonStatus(str, enum.Enum):
    """Publication lifecycle of a Lesson (M11).

    Deliberately separate from ``AcademicStatus``: a Lesson is not
    archived, it is a draft or it is published. Unpublishing a Lesson
    returns it to ``draft``; there is no hard delete and no archived
    Lesson state.
    """

    DRAFT = "draft"
    PUBLISHED = "published"


class MaterialKind(str, enum.Enum):
    """What a Lesson Material *is* (M12). Immutable after creation -- each
    kind carries exactly one payload (see the ``materials`` payload CHECK).
    """

    RICH_TEXT = "rich_text"
    EXTERNAL_LINK = "external_link"
    FILE = "file"


class FileCategory(str, enum.Enum):
    """Broad type of an uploaded file (M12), derived server-side from the
    validated extension -- never from the browser."""

    DOCUMENT = "document"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


class FileAccessAction(str, enum.Enum):
    """An entry in the append-only ``file_access_logs`` audit trail (M12)."""

    UPLOAD = "upload"
    INLINE = "inline"
    DOWNLOAD = "download"


class NotificationKind(str, enum.Enum):
    """What a stored in-app :class:`~app.models.notification.Notification`
    is about (M14).

    Deliberately a small, closed set covering exactly the high-signal
    domain events that exist today -- there is no generic
    "other"/"custom" kind and no placeholder for a future Assignment /
    Quiz / Attendance / Grade / Calendar / messaging producer. Adding a
    kind is a schema change (the ``notifications`` ``kind`` CHECK
    constraint), which is the point: an unrecognised kind can never be
    inserted by application code or by a manual row.

    ``ANNOUNCEMENT_PUBLISHED`` is the one member Phase 4 / M09 adds, and
    it is added the only way a member may be: with a migration that
    rewrites the ``kind`` CHECK. It fires **once**, on an Announcement's
    first successful publication, and never again -- not on a retry, a
    refresh, an edit or a withdrawal. See
    ``app/services/notification_delivery.py``.
    """

    ENROLLMENT_ACTIVATED = "enrollment_activated"
    ENROLLMENT_WITHDRAWN = "enrollment_withdrawn"
    TEACHER_ASSIGNMENT_ACTIVATED = "teacher_assignment_activated"
    TEACHER_ASSIGNMENT_REMOVED = "teacher_assignment_removed"
    SCHEDULE_CHANGED = "schedule_changed"
    LESSON_PUBLISHED = "lesson_published"
    MATERIAL_AVAILABLE = "material_available"
    ANNOUNCEMENT_PUBLISHED = "announcement_published"


class AssignmentStatus(str, enum.Enum):
    """Publication lifecycle of a Group-owned Assignment (Phase 4 / M01).

    Deliberately its own closed set rather than a reuse of
    ``LessonStatus``: the two objects publish independently and a future
    change to one must never silently redefine the other. It is equally
    deliberately *not* ``AcademicStatus`` -- an Assignment is a draft or
    it is published; it is never archived, and Phase 4 / M01 has no hard
    delete.

    ``Scheduled`` / ``Open`` / ``Past due`` are **not** members here.
    They are derived at read time from ``opens_at`` / ``due_at`` against
    one injected reference moment, never stored, so time passing can
    never leave a stale status behind.
    """

    DRAFT = "draft"
    PUBLISHED = "published"


class QuestionAnswerMode(str, enum.Enum):
    """How many options a Teacher-authored multiple-choice question counts
    as correct (Phase 4 / M04B).

    Deliberately its own closed set, and deliberately **not** a "question
    type": every question in M04B is multiple choice. What this names is
    the *answer cardinality rule* the Teacher chose, and the two members
    are the two the owner approved:

    - ``SINGLE`` -- exactly one active option is correct;
    - ``MULTIPLE`` -- at least two active options are correct, and every
      active option being correct is legitimate. No distractor is
      required, because no such business rule was approved.

    There is no ``TRUE_FALSE``, ``SHORT_ANSWER``, ``FILL_IN_BLANK`` or
    ``MATCHING`` member and no placeholder for one: those are separate
    question types, not answer modes, and none is approved. Adding a
    member is a schema change (the ``quiz_questions.answer_mode`` CHECK),
    which is the point -- an unrecognised mode can never be inserted by
    application code or by a manual row.

    Nothing here carries a score, a weight or any grading meaning.
    """

    SINGLE = "single"
    MULTIPLE = "multiple"


class QuizStatus(str, enum.Enum):
    """Publication lifecycle of a Group-owned Quiz (Phase 4 / M04D).

    Deliberately its own closed set rather than a reuse of
    ``AssignmentStatus`` or ``LessonStatus``: the three objects publish
    independently, and a future change to one must never silently
    redefine another. It is equally deliberately *not* ``AcademicStatus``
    -- a Quiz is a draft or it is published; it is never archived, and
    there is no hard delete anywhere in the Quiz aggregate.

    ``Scheduled`` / ``Open`` / ``Closed`` are **not** members here. They
    are derived at read time from ``opens_at`` / ``closes_at`` against one
    injected reference moment, never stored, so time passing can never
    leave a stale status behind -- the same rule M01 applies to
    Assignments.

    There is no ``archived``, ``closed`` or ``graded`` member and no
    placeholder for one.
    """

    DRAFT = "draft"
    PUBLISHED = "published"


class QuizAttemptStatus(str, enum.Enum):
    """Lifecycle of one Student's attempt at a published Quiz
    (Phase 4 / M04D).

    - ``IN_PROGRESS`` -- the Student may still navigate questions and
      replace saved selections. Exactly one such attempt may exist per
      Student and Quiz.
    - ``SUBMITTED`` -- the Student finalized it. Graded, frozen, and
      never writable again.
    - ``EXPIRED`` -- the authoritative deadline passed while it was still
      in progress. Finalized **once** by the next request that observes
      it under the required locks, graded on whatever was saved, and then
      equally frozen. There is no background job.

    Both terminal states are graded and immutable; they differ only in
    *how* the attempt ended, which is information the Student and the
    Teacher both deserve. There is deliberately no ``abandoned``,
    ``paused``, ``graded`` or ``released`` member: grading happens
    exactly once at finalization, and no manual-grading or answer-release
    workflow exists.
    """

    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    EXPIRED = "expired"


class TranscriptVisibility(str, enum.Enum):
    """Who may read a Listening activity's authored transcript, and when
    (Phase 4 / M05).

    Deliberately its own closed set and deliberately **not** a boolean: a
    Teacher who wants the transcript released only *after* a Student has
    finished needs a third answer, and squeezing that into "shown / not
    shown" would have meant inventing an implicit rule somewhere else.

    - ``hidden`` -- Students never receive the transcript. It is not
      rendered, not placed in a URL, a token, a hidden field or a
      JavaScript value, and not returned by any audio response.
    - ``after_submission`` -- Students receive it only on a **finalized**
      attempt's own result page. An attempt still in progress is not a
      finished one, so it does not qualify.
    - ``always`` -- Students may read it on the Listening detail page and
      while answering.

    A Teacher assigned to the Group always sees the transcript they
    configured, whatever this says: the policy governs Student access, not
    authoring.

    An **empty** transcript is legitimate under every member -- a Teacher
    may set a policy before writing anything, and nothing is fabricated to
    fill the gap.

    There is no ``after_close``, ``on_request`` or per-Student member and
    no placeholder for one. Adding a member is a schema change (the
    ``listening_activities.transcript_visibility`` CHECK), which is the
    point: an unrecognised policy can never be inserted by application
    code or by a manual row, and never silently defaults to the most
    permissive answer.
    """

    HIDDEN = "hidden"
    AFTER_SUBMISSION = "after_submission"
    ALWAYS = "always"


class AttendanceStatus(str, enum.Enum):
    """How one captured Student was marked for one attendance session
    (Phase 4 / M07).

    Exactly the four members the owner approved, and deliberately a
    closed set: an unrecognised value can never be inserted by
    application code or by a manual row, because the
    ``attendance_records.status`` CHECK names these four and nothing
    else. Adding a member is therefore a schema change, which is the
    point.

    - ``present`` -- the Student attended.
    - ``absent`` -- the Student did not attend. This is also the
      **default** every captured record starts at, so a session a Teacher
      has opened but not yet worked through never silently claims that
      somebody was there.
    - ``late`` -- the Student attended, but not from the start. It is a
      *mark*, nothing more: no minutes are stored, no lateness threshold
      exists, and nothing derives a penalty from it.
    - ``excused`` -- the Student did not attend and the Teacher recorded
      that the absence was excused. It carries no approval workflow, no
      document, no request record and no separate reviewer.

    There is no ``unknown``, ``pending``, ``not_marked``, ``left_early``,
    ``sick``, ``holiday`` or ``partial`` member and no placeholder for
    one. There is equally deliberately **no ordering, weight, score,
    percentage or pass/fail meaning** attached to any member here or
    anywhere else: Grades are an undecided module, and nothing in M07
    turns an attendance mark into a number that could look like one.
    """

    PRESENT = "present"
    ABSENT = "absent"
    LATE = "late"
    EXCUSED = "excused"


class GradeSourceKind(str, enum.Enum):
    """What one :class:`~app.models.grade_item.GradeItem` is a grade
    *for* (Phase 4 / M08).

    Exactly the five members the owner approved, and deliberately a
    closed set: an unrecognised value can never be inserted by
    application code or by a manual row, because the
    ``grade_items.source_kind`` CHECK names these five and nothing else.
    Adding a member is therefore a schema change, which is the point.

    - ``assignment`` -- the grade is for one of the Group's own ordinary
      :class:`~app.models.assignment.Assignment` rows. Exactly one
      ``assignment_id`` is stored and the other two source columns are
      NULL.
    - ``quiz`` -- the grade is for one of the Group's own
      :class:`~app.models.quiz.Quiz` rows. A Phase 4 / M05 Listening
      activity **is** a Quiz (the ``listening_activities`` row is an
      extension of it, exactly as a Speaking activity is an extension of
      an Assignment), so a Listening grade is a ``quiz`` grade here and
      there is deliberately no separate ``listening`` member.
    - ``speaking`` -- the grade is for one of the Group's own
      :class:`~app.models.speaking_activity.SpeakingActivity` rows.
    - ``activity`` -- classroom work that has no row anywhere in this
      system: participation in a debate, a poster, a presentation the
      Teacher graded in the room. All three source columns are NULL.
    - ``manual`` -- anything else the Teacher decided to grade, equally
      with no linked row.

    ``activity`` and ``manual`` are deliberately **two** members rather
    than one: they are indistinguishable to the schema (both store no
    link), but they are not the same statement to a Teacher reading a
    gradebook back a term later, and collapsing them would throw that
    distinction away with nothing to recover it from.

    There is no ``attendance``, ``submission``, ``exam``, ``midterm``,
    ``final``, ``project`` or ``bonus`` member and no placeholder for
    one. **Attendance in particular is absent on purpose**: Phase 4 / M07
    states that no attendance mark carries a weight, a score or a
    pass/fail meaning, and M08 does not quietly reverse that by giving
    attendance a grade source of its own. A Teacher who wants to grade
    participation records it as an ``activity`` they entered themselves.

    Nothing here imports, copies or derives a score from the linked row.
    A ``quiz`` GradeItem does **not** pull
    :class:`~app.models.quiz_attempt.QuizAttempt` results in: the link
    says what the grade is *about*, and the number is the one a Teacher
    deliberately entered.
    """

    ASSIGNMENT = "assignment"
    QUIZ = "quiz"
    SPEAKING = "speaking"
    ACTIVITY = "activity"
    MANUAL = "manual"


class AnnouncementScope(str, enum.Enum):
    """Who one :class:`~app.models.announcement.Announcement` is addressed
    to (Phase 4 / M09).

    Exactly the three members the owner approved, and deliberately a
    closed set: an unrecognised value can never be inserted by
    application code or by a manual row, because the
    ``announcements.scope`` CHECK names these three and nothing else.
    Adding a member is therefore a schema change, which is the point.

    - ``center`` -- everybody at the center. Carries **no** target: both
      ``course_id`` and ``group_id`` are NULL.
    - ``course`` -- everybody currently reachable through one Course,
      i.e. every Student actively enrolled in, and every Teacher actively
      assigned to, an operational Group of that Course. Carries exactly
      ``course_id``.
    - ``group`` -- exactly one Group's current members. Carries exactly
      ``group_id``, and **never** a duplicated ``course_id``: the Course
      of a group-scoped announcement is ``group.course``, one answer
      rather than two that could disagree.

    There is no ``level``, ``term``, ``role``, ``user``, ``teachers`` or
    ``students`` member and no placeholder for one. An announcement is a
    communication addressed at an academic place, never at a person or at
    a role: a message to one person is messaging, which is an undecided
    module.
    """

    CENTER = "center"
    COURSE = "course"
    GROUP = "group"


class AnnouncementStatus(str, enum.Enum):
    """Lifecycle of one Announcement (Phase 4 / M09).

    Deliberately its own closed set rather than a reuse of
    ``AssignmentStatus`` / ``QuizStatus`` / ``LessonStatus``: those three
    publish and *unpublish*, and an announcement may never do the second.
    It is equally deliberately not ``AcademicStatus`` -- an announcement
    is not archived, and there is no hard delete anywhere in M09.

    - ``draft`` -- author-only working text. Both publication timestamps
      are NULL. It may be edited freely and published.
    - ``published`` -- readable by everybody the scope currently reaches.
      ``published_at`` is set, ``withdrawn_at`` is NULL. Its title, body,
      scope, target and author are **frozen**; the only transition left
      is withdrawal.
    - ``withdrawn`` -- permanently hidden from every reader. Both
      timestamps are set and ``withdrawn_at >= published_at``. This is a
      **terminal** state: a withdrawn announcement can never be edited,
      republished, restored or deleted. Correcting one means writing a
      new announcement, so the record of what was actually said -- and
      for how long -- survives.

    There is no ``scheduled``, ``archived``, ``deleted``, ``expired`` or
    ``pending`` member and no placeholder for one. In particular there is
    no scheduled publication anywhere in M09: a publication time that has
    not happened yet would be a promise no process in this application
    keeps, and ``published_at`` is only ever the moment a human pressed
    publish.
    """

    DRAFT = "draft"
    PUBLISHED = "published"
    WITHDRAWN = "withdrawn"


class CalendarEventStatus(str, enum.Enum):
    """Lifecycle of one center-wide CalendarEvent (Phase 4 / M10).

    Deliberately its own closed set, and deliberately a **two**-member
    one. A center event is not authored in private and then released:
    there is no ``draft`` state, because an event an Administrator has
    typed into the center's calendar is the center's calendar. There is
    likewise no ``archived``, ``deleted``, ``completed`` or ``expired``
    member and no placeholder for one -- an event in the past is simply
    an event whose date has passed, which the date already says, and
    storing a second opinion about it would let the two disagree.

    - ``scheduled`` -- the event stands. ``cancelled_at`` is NULL. Every
      active Student and Teacher sees it inside the date range they are
      looking at, and an Administrator may still edit it.
    - ``cancelled`` -- the event is off. ``cancelled_at`` is set, and the
      row is **permanently immutable**: there is no restore, no
      un-cancel, no re-schedule and no edit, and no endpoint exists
      server-side for one. It disappears from every Student and Teacher
      calendar the instant the cancellation commits, and stays visible --
      clearly marked -- only on the Administrator surfaces, so the record
      of what was announced and then called off survives.

    Cancellation stays possible for an event whose date is already in the
    past: a calendar is also a record, and an Administrator must be able
    to mark something as not having happened.
    """

    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"
