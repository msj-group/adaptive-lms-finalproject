from app.models.user import User, UserRole, UserStatus
from app.models.account_revision import AccountRevision
from app.models.room import Room
from app.models.scheduling_revision import SchedulingRevision
from app.models.enums import (
    AcademicStatus,
    AnnouncementScope,
    AnnouncementStatus,
    AssignmentStatus,
    AttendanceStatus,
    CalendarEventStatus,
    DiscussionTopicStatus,
    FeePlanItemKind,
    FeePlanItemStatus,
    FeePlanStatus,
    FileAccessAction,
    FileCategory,
    GradeSourceKind,
    InvoiceItemKind,
    InvoiceItemStatus,
    InvoiceStatus,
    LessonStatus,
    MaterialKind,
    NotificationKind,
    PaymentAuditEventKind,
    PaymentIntentStatus,
    PaymentMethod,
    PaymentTransactionKind,
    PaymentTransactionStatus,
    ProviderEventOutcome,
    ProviderEventType,
    QuestionAnswerMode,
    QuizAttemptStatus,
    QuizStatus,
    ReceiptStatus,
    ResearchAuditAction,
    ResearchAuditChannel,
    ResearchCollectionStatus,
    ResearchConfigurationStatus,
    ResearchDeferralReason,
    ResearchPromptStatus,
    ResearchProvenance,
    ResearchSamplingReason,
    ResearchSessionEndReason,
    ResearchStatusBasis,
    StudentFeeAssignmentStatus,
    TranscriptVisibility,
)
from app.models.academic_term import AcademicTerm
from app.models.level import Level
from app.models.course import Course
from app.models.group import Group
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.enrollment_history import EnrollmentMembership, EnrollmentEvent
from app.models.group_teacher_assignment import GroupTeacherAssignment, GroupTeacherAssignmentStatus
from app.models.schedule import Schedule
from app.models.unit import Unit
from app.models.lesson import Lesson
from app.models.lesson_progress import LessonProgress
from app.models.uploaded_file import UploadedFile
from app.models.material import Material
from app.models.file_access_log import FileAccessLog
from app.models.notification import Notification
from app.models.assignment import Assignment
from app.models.submission import ANSWER_MAX_LENGTH, Submission
from app.models.submission_feedback import FEEDBACK_MAX_LENGTH, SubmissionFeedback
from app.models.quiz import (
    MAX_ATTEMPT_LIMIT,
    MAX_QUIZ_QUESTIONS,
    MAX_TIME_LIMIT_MINUTES,
    MIN_ATTEMPT_LIMIT,
    MIN_TIME_LIMIT_MINUTES,
    QUIZ_INSTRUCTIONS_MAX_LENGTH,
    QUIZ_TITLE_MAX_LENGTH,
    Quiz,
)
from app.models.quiz_question import QUESTION_PROMPT_MAX_LENGTH, QuizQuestion
from app.models.question_option import (
    MAX_ACTIVE_OPTIONS,
    MIN_ACTIVE_OPTIONS,
    OPTION_TEXT_MAX_LENGTH,
    QuestionOption,
)
from app.models.quiz_attempt import QuizAttempt
from app.models.quiz_answer import QuizAnswer, QuizAnswerSelection
from app.models.listening_activity import (
    TRANSCRIPT_MAX_LENGTH,
    VOCABULARY_NOTES_MAX_LENGTH,
    ListeningActivity,
)
from app.models.speaking_activity import SpeakingActivity
from app.models.speaking_submission import SpeakingSubmission
from app.models.speaking_feedback import (
    SPEAKING_FEEDBACK_MAX_LENGTH,
    SpeakingFeedback,
)
from app.models.attendance_session import AttendanceSession
from app.models.attendance_record import (
    ATTENDANCE_NOTE_MAX_LENGTH,
    DEFAULT_ATTENDANCE_STATUS,
    AttendanceRecord,
)
from app.models.grade_category import (
    BASIS_POINTS_TOTAL,
    GRADE_CATEGORY_TITLE_MAX_LENGTH,
    MIN_CATEGORY_BASIS_POINTS,
    GradeCategory,
)
from app.models.grade_item import (
    GRADE_ITEM_TITLE_MAX_LENGTH,
    MAX_POINTS_CEILING,
    MIN_POINTS,
    POINTS_PRECISION,
    POINTS_SCALE,
    GradeItem,
)
from app.models.grade_record import GRADE_COMMENT_MAX_LENGTH, GradeRecord
from app.models.announcement import (
    ANNOUNCEMENT_BODY_MAX_LENGTH,
    ANNOUNCEMENT_TITLE_MAX_LENGTH,
    Announcement,
)
from app.models.calendar_event import (
    CALENDAR_EVENT_DETAILS_MAX_LENGTH,
    CALENDAR_EVENT_LOCATION_MAX_LENGTH,
    CALENDAR_EVENT_TITLE_MAX_LENGTH,
    CalendarEvent,
)
from app.models.message_thread import (
    MESSAGE_SUBJECT_MAX_LENGTH,
    MessageThread,
    MessageThreadMember,
)
from app.models.message import MESSAGE_BODY_MAX_LENGTH, Message
from app.models.discussion_topic import (
    DISCUSSION_BODY_MAX_LENGTH,
    DISCUSSION_TITLE_MAX_LENGTH,
    DiscussionTopic,
)
from app.models.discussion_reply import DiscussionReply
from app.models.fee_plan import (
    FEE_PLAN_DESCRIPTION_MAX_LENGTH,
    FEE_PLAN_NAME_MAX_LENGTH,
    FeePlan,
)
from app.models.fee_plan_item import (
    FEE_PLAN_ITEM_LABEL_MAX_LENGTH,
    MAX_ACTIVE_FEE_PLAN_ITEMS,
    FeePlanItem,
)
from app.models.student_fee_assignment import StudentFeeAssignment
from app.models.invoice import (
    MAX_INVOICE_SEQUENCE_NUMBER,
    FinancialHistoryError,
    Invoice,
)
from app.models.invoice_item import (
    INVOICE_ITEM_LABEL_MAX_LENGTH,
    MAX_ACTIVE_INVOICE_ITEMS,
    MAX_INVOICE_ITEM_ROWS,
    InvoiceItem,
)
from app.models.invoice_number_sequence import InvoiceNumberSequence
from app.models.receipt_number_sequence import MAX_RECEIPT_SEQUENCE_NUMBER, ReceiptNumberSequence
from app.models.payment_audit_event import INVOICE_AUDIT_REASON_MAX_LENGTH, PaymentAuditEvent
from app.models.payment_transaction import (
    BANK_TRANSFER_REFERENCE_MAX_LENGTH,
    MAX_INVOICE_COLLECTIONS,
    MAX_INVOICE_PAYMENT_ROWS,
    PAYMENT_REASON_MAX_LENGTH,
    PaymentTransaction,
)
from app.models.financial_revision import FinancialRevision
from app.models.receipt import Receipt
from app.models.payment_intent import (
    ACTIVE_PAYMENT_INTENT_STATUSES,
    MAX_INVOICE_PAYMENT_INTENTS,
    PAYMENT_INTENT_PROVIDERS,
    TERMINAL_PAYMENT_INTENT_STATUSES,
    PaymentIntent,
)
from app.models.payment_provider_event import PaymentProviderEvent
from app.models.research_common import (
    SUBJECT_CODE_ALPHABET,
    SUBJECT_CODE_LENGTH,
    SUBJECT_CODE_PREFIX,
    ResearchDataError,
    generate_subject_code,
    now_ms,
)
from app.models.research_subject import ResearchSubject
from app.models.research_subject_link import ResearchSubjectLink
from app.models.research_configuration import (
    CONFIGURATION_CURRENT_MARKER,
    CONFIGURATION_LABEL_MAX_LENGTH,
    POLICY_BOUNDS,
    ResearchConfiguration,
)
from app.models.research_session import ResearchSession
from app.models.research_event import ResearchEvent
from app.models.research_feedback_prompt import (
    CAUSE_CODES,
    CAUSE_COLUMNS,
    RATING_ANCHORS,
    ResearchFeedbackPrompt,
)
from app.models.research_export import EXPORT_FORMAT, ResearchExport
from app.models.research_export_archive import MAX_EXPORT_ARCHIVE_BYTES, ResearchExportArchive
from app.models.research_audit_event import ResearchAuditEvent

__all__ = [
    "User",
    "AccountRevision",
    "Room",
    "SchedulingRevision",
    "UserRole",
    "UserStatus",
    "AcademicStatus",
    "AssignmentStatus",
    "AttendanceStatus",
    "LessonStatus",
    "MaterialKind",
    "NotificationKind",
    "FileCategory",
    "FileAccessAction",
    "AcademicTerm",
    "Level",
    "Course",
    "Group",
    "Enrollment",
    "EnrollmentMembership",
    "EnrollmentEvent",
    "EnrollmentStatus",
    "GroupTeacherAssignment",
    "GroupTeacherAssignmentStatus",
    "Schedule",
    "Unit",
    "Lesson",
    "LessonProgress",
    "UploadedFile",
    "Material",
    "FileAccessLog",
    "Notification",
    "Assignment",
    "Submission",
    "ANSWER_MAX_LENGTH",
    "SubmissionFeedback",
    "FEEDBACK_MAX_LENGTH",
    "Quiz",
    "QUIZ_INSTRUCTIONS_MAX_LENGTH",
    "QUIZ_TITLE_MAX_LENGTH",
    "QuestionAnswerMode",
    "QuizQuestion",
    "QUESTION_PROMPT_MAX_LENGTH",
    "QuestionOption",
    "OPTION_TEXT_MAX_LENGTH",
    "MIN_ACTIVE_OPTIONS",
    "MAX_ACTIVE_OPTIONS",
    "QuizStatus",
    "MIN_TIME_LIMIT_MINUTES",
    "MAX_TIME_LIMIT_MINUTES",
    "MIN_ATTEMPT_LIMIT",
    "MAX_ATTEMPT_LIMIT",
    "MAX_QUIZ_QUESTIONS",
    "QuizAttemptStatus",
    "QuizAttempt",
    "QuizAnswer",
    "QuizAnswerSelection",
    "TranscriptVisibility",
    "ListeningActivity",
    "TRANSCRIPT_MAX_LENGTH",
    "VOCABULARY_NOTES_MAX_LENGTH",
    "SpeakingActivity",
    "SpeakingSubmission",
    "SpeakingFeedback",
    "SPEAKING_FEEDBACK_MAX_LENGTH",
    "AttendanceSession",
    "AttendanceRecord",
    "ATTENDANCE_NOTE_MAX_LENGTH",
    "DEFAULT_ATTENDANCE_STATUS",
    "GradeSourceKind",
    "GradeCategory",
    "GRADE_CATEGORY_TITLE_MAX_LENGTH",
    "BASIS_POINTS_TOTAL",
    "MIN_CATEGORY_BASIS_POINTS",
    "GradeItem",
    "GRADE_ITEM_TITLE_MAX_LENGTH",
    "POINTS_PRECISION",
    "POINTS_SCALE",
    "MAX_POINTS_CEILING",
    "MIN_POINTS",
    "GradeRecord",
    "GRADE_COMMENT_MAX_LENGTH",
    "AnnouncementScope",
    "AnnouncementStatus",
    "Announcement",
    "ANNOUNCEMENT_TITLE_MAX_LENGTH",
    "ANNOUNCEMENT_BODY_MAX_LENGTH",
    "CalendarEventStatus",
    "CalendarEvent",
    "CALENDAR_EVENT_TITLE_MAX_LENGTH",
    "CALENDAR_EVENT_DETAILS_MAX_LENGTH",
    "CALENDAR_EVENT_LOCATION_MAX_LENGTH",
    "MessageThread",
    "MessageThreadMember",
    "MESSAGE_SUBJECT_MAX_LENGTH",
    "Message",
    "MessageChange",
    "MessageThreadClear",
    "MESSAGE_BODY_MAX_LENGTH",
    "DiscussionTopicStatus",
    "DiscussionTopic",
    "DiscussionReply",
    "DISCUSSION_TITLE_MAX_LENGTH",
    "DISCUSSION_BODY_MAX_LENGTH",
    "FeePlanStatus",
    "FeePlanItemKind",
    "FeePlanItemStatus",
    "FeePlan",
    "FEE_PLAN_NAME_MAX_LENGTH",
    "FEE_PLAN_DESCRIPTION_MAX_LENGTH",
    "FeePlanItem",
    "FEE_PLAN_ITEM_LABEL_MAX_LENGTH",
    "MAX_ACTIVE_FEE_PLAN_ITEMS",
    "StudentFeeAssignmentStatus",
    "StudentFeeAssignment",
    "InvoiceStatus",
    "InvoiceItemKind",
    "InvoiceItemStatus",
    "PaymentAuditEventKind",
    "Invoice",
    "MAX_INVOICE_SEQUENCE_NUMBER",
    "FinancialHistoryError",
    "FinancialRevision",
    "InvoiceItem",
    "INVOICE_ITEM_LABEL_MAX_LENGTH",
    "MAX_ACTIVE_INVOICE_ITEMS",
    "MAX_INVOICE_ITEM_ROWS",
    "InvoiceNumberSequence",
    "PaymentAuditEvent",
    "INVOICE_AUDIT_REASON_MAX_LENGTH",
    "PaymentTransactionKind",
    "PaymentMethod",
    "PaymentTransactionStatus",
    "ReceiptStatus",
    "PaymentTransaction",
    "BANK_TRANSFER_REFERENCE_MAX_LENGTH",
    "MAX_INVOICE_COLLECTIONS",
    "MAX_INVOICE_PAYMENT_ROWS",
    "PAYMENT_REASON_MAX_LENGTH",
    "Receipt",
    "ReceiptNumberSequence",
    "MAX_RECEIPT_SEQUENCE_NUMBER",
    "PaymentIntentStatus",
    "PaymentIntent",
    "ACTIVE_PAYMENT_INTENT_STATUSES",
    "TERMINAL_PAYMENT_INTENT_STATUSES",
    "MAX_INVOICE_PAYMENT_INTENTS",
    "PAYMENT_INTENT_PROVIDERS",
    "ProviderEventType",
    "ProviderEventOutcome",
    "PaymentProviderEvent",
    "ResearchCollectionStatus",
    "ResearchStatusBasis",
    "ResearchProvenance",
    "ResearchConfigurationStatus",
    "ResearchSessionEndReason",
    "ResearchPromptStatus",
    "ResearchSamplingReason",
    "ResearchDeferralReason",
    "ResearchAuditAction",
    "ResearchAuditChannel",
    "ResearchDataError",
    "generate_subject_code",
    "now_ms",
    "SUBJECT_CODE_PREFIX",
    "SUBJECT_CODE_ALPHABET",
    "SUBJECT_CODE_LENGTH",
    "ResearchSubject",
    "ResearchSubjectLink",
    "ResearchConfiguration",
    "CONFIGURATION_CURRENT_MARKER",
    "CONFIGURATION_LABEL_MAX_LENGTH",
    "POLICY_BOUNDS",
    "ResearchSession",
    "ResearchEvent",
    "ResearchFeedbackPrompt",
    "CAUSE_CODES",
    "CAUSE_COLUMNS",
    "RATING_ANCHORS",
    "ResearchExport",
    "ResearchExportArchive",
    "MAX_EXPORT_ARCHIVE_BYTES",
    "EXPORT_FORMAT",
    "ResearchAuditEvent",
]

from app.models.academic_revision import AcademicRevision
from app.models.attempt_submission_receipt import AttemptSubmissionReceipt
from app.models.research_storage import ResearchConfigurationSequence, ResearchDataGap, ResearchExportSession
from app.services import history_write_guards
from app.models.message_change import MessageChange, MessageThreadClear
from app.services import attempt_finalization_guards
from app.services.academic_episode_binding import register_episode_events
register_episode_events(
    [Submission, SpeakingSubmission, QuizAttempt, AttendanceRecord, GradeRecord, LessonProgress],
    [GradeRecord, AttendanceRecord, SubmissionFeedback, SpeakingFeedback],
)
