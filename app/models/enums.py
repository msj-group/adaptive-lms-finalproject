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
