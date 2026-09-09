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
    domain events that already exist through M13 -- there is no generic
    "other"/"custom" kind, no Announcement kind, and no placeholder for a
    future Assignment / Quiz / Attendance / Grade / Calendar / messaging
    producer. Adding a kind is a schema change (the ``notifications``
    ``kind`` CHECK constraint), which is the point: an unrecognised kind
    can never be inserted by application code or by a manual row.
    """

    ENROLLMENT_ACTIVATED = "enrollment_activated"
    ENROLLMENT_WITHDRAWN = "enrollment_withdrawn"
    TEACHER_ASSIGNMENT_ACTIVATED = "teacher_assignment_activated"
    TEACHER_ASSIGNMENT_REMOVED = "teacher_assignment_removed"
    SCHEDULE_CHANGED = "schedule_changed"
    LESSON_PUBLISHED = "lesson_published"
    MATERIAL_AVAILABLE = "material_available"


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
