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
