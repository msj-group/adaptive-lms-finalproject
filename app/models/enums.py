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

    ``MESSAGE_RECEIVED`` is the member Phase 4 / M11 adds, the same way:
    one row for the *other* member of a private thread, written after a
    new message has committed. It names the sender and the thread subject
    only -- never the message body.

    ``DISCUSSION_TOPIC_CREATED`` is the member Phase 4 / M12 adds, the same
    way: one row per active Student currently enrolled in the topic's
    operational Group, written once, after a Teacher's new topic has
    committed. It names the Group and the topic title only -- never the
    topic body or any reply -- and nothing is sent for a reply, a lock, a
    reopen or a replayed form.
    """

    ENROLLMENT_ACTIVATED = "enrollment_activated"
    ENROLLMENT_WITHDRAWN = "enrollment_withdrawn"
    TEACHER_ASSIGNMENT_ACTIVATED = "teacher_assignment_activated"
    TEACHER_ASSIGNMENT_REMOVED = "teacher_assignment_removed"
    SCHEDULE_CHANGED = "schedule_changed"
    LESSON_PUBLISHED = "lesson_published"
    MATERIAL_AVAILABLE = "material_available"
    ANNOUNCEMENT_PUBLISHED = "announcement_published"
    MESSAGE_RECEIVED = "message_received"
    DISCUSSION_TOPIC_CREATED = "discussion_topic_created"


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


class DiscussionTopicStatus(str, enum.Enum):
    """Lock state of one Group discussion topic (Phase 4 / M12).

    Deliberately its own closed set, and deliberately a **two**-member one.
    It is not ``AcademicStatus`` -- a topic is never archived -- and it is
    not a publication lifecycle: a topic is readable by its Group from the
    moment a Teacher creates it.

    - ``open`` -- the Group's currently eligible Students and Teachers may
      read the topic and add replies.
    - ``locked`` -- a Teacher assigned to the Group closed it. It stays
      readable to the same people, exactly as it was, and accepts no new
      reply; a Teacher assigned to the Group may reopen it.

    Locking is moderation of the conversation, never removal of it: there
    is no ``hidden``, ``deleted``, ``archived``, ``pinned`` or ``draft``
    member and no placeholder for one. Adding a member is a schema change
    (the ``discussion_topics.status`` CHECK), which is the point.
    """

    OPEN = "open"
    LOCKED = "locked"


class FeePlanStatus(str, enum.Enum):
    """Lifecycle of one administrative
    :class:`~app.models.fee_plan.FeePlan` (Phase 5 / M02).

    - ``draft`` -- never activated. An active Administrator may still
      change the plan's name and description and add, edit or remove its
      items.
    - ``active`` -- available for later use. Its financial definition --
      the plan and every item -- is frozen **permanently** from the first
      activation onward.
    - ``archived`` -- unavailable and read-only until restored. A plan
      that was ever activated is restored to ``active`` and stays frozen;
      a draft archived before it was ever activated is restored to
      ``draft`` and becomes editable again (Phase 5 / M02R).

    There is no ``deleted`` member and no placeholder for one: nothing in
    the fee plan catalogue is ever physically deleted. Adding a member is
    a schema change (the ``fee_plans.status`` CHECK), which is the point.
    """

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class FeePlanItemKind(str, enum.Enum):
    """What one :class:`~app.models.fee_plan_item.FeePlanItem` charges for
    (Phase 5 / M02).

    Exactly the two approved kinds. Discounts, installments, scholarships,
    exemptions and taxes are deferred and have no member here; adding one
    is a schema change (the ``fee_plan_items.kind`` CHECK).
    """

    REGISTRATION = "registration"
    COURSE = "course"


class FeePlanItemStatus(str, enum.Enum):
    """Whether one :class:`~app.models.fee_plan_item.FeePlanItem` is part
    of its plan's definition (Phase 5 / M02).

    - ``active`` -- counted in the plan's items and its total.
    - ``removed`` -- taken out of a **draft** plan. The row stays as
      history with its removal attribution; it is never deleted, never
      restored and never edited again.
    """

    ACTIVE = "active"
    REMOVED = "removed"


class StudentFeeAssignmentStatus(str, enum.Enum):
    """Lifecycle of one
    :class:`~app.models.student_fee_assignment.StudentFeeAssignment`
    (Phase 5 / M03).

    - ``assigned`` -- the Enrollment's current fee plan. An Enrollment has at
      most one assigned row at a time; that rule is proved by the
      application under the Enrollment lock, because MySQL has no portable
      partial unique index for it.
    - ``cancelled`` -- an Administrator cancelled the assignment explicitly.
      Terminal: the row is kept as history with its cancellation
      attribution, and is never edited, restored or deleted. Assigning a fee
      plan again inserts a new row.

    The only transition is ``assigned -> cancelled``. There is no
    ``invoiced``, ``paid``, ``transferred`` or ``deleted`` member and no
    placeholder for one. Adding a member is a schema change (the
    ``student_fee_assignments.status`` CHECK), which is the point.
    """

    ASSIGNED = "assigned"
    CANCELLED = "cancelled"


class InvoiceStatus(str, enum.Enum):
    """Lifecycle of one :class:`~app.models.invoice.Invoice` (Phase 5 / M04).

    - ``draft`` -- copied from the assignment's fee plan and under review.
      Its lines may be added, edited and removed; it has no invoice number.
    - ``issued`` -- issued manually by an Administrator, which allocated its
      permanent ``INV-YYYY-NNNNNN`` number. Its lines stay editable, each
      change needing a reason and recorded by an audit event, until a
      ``pending`` or ``confirmed`` payment exists for it (Phase 5 / M05):
      from then on its lines and its cancellation are frozen.
    - ``cancelled`` -- terminal and read-only. A cancelled issued invoice
      keeps its number; a cancelled draft never had one.

    ``draft -> issued -> cancelled`` and ``draft -> cancelled`` are the only
    transitions. There is no ``paid``, ``void``, ``refunded`` or ``deleted``
    member and no placeholder for one. Adding a member is a schema change
    (the ``invoices.status`` CHECK), which is the point.
    """

    DRAFT = "draft"
    ISSUED = "issued"
    CANCELLED = "cancelled"


class InvoiceItemKind(str, enum.Enum):
    """What one :class:`~app.models.invoice_item.InvoiceItem` charges for
    (Phase 5 / M04).

    Exactly the fee plan item kinds, so every plan line can be copied. There
    is no discount, tax, installment or credit member; adding one is a schema
    change (the ``invoice_items.kind`` CHECK).
    """

    REGISTRATION = "registration"
    COURSE = "course"


class InvoiceItemStatus(str, enum.Enum):
    """Whether one :class:`~app.models.invoice_item.InvoiceItem` is part of
    its invoice (Phase 5 / M04).

    - ``active`` -- counted in the invoice's lines and its total.
    - ``removed`` -- taken out of a draft or issued invoice. The row stays
      as history with its removal attribution and never changes again.
    """

    ACTIVE = "active"
    REMOVED = "removed"


class PaymentAuditEventKind(str, enum.Enum):
    """What one append-only
    :class:`~app.models.payment_audit_event.PaymentAuditEvent` records.

    Invoice movements (Phase 5 / M04):

    - ``invoice_draft_created`` -- a draft was copied from the fee plan.
    - ``invoice_draft_edited`` -- a draft's line was added, edited or removed.
    - ``invoice_issued`` -- the draft was issued and numbered.
    - ``invoice_issued_edited`` -- an issued invoice's line was added, edited
      or removed; a reason is required.
    - ``invoice_cancelled`` -- the invoice was cancelled; a reason is
      required.

    Manual payment and receipt movements (Phase 5 / M05):

    - ``payment_cash_recorded`` -- a cash collection was recorded, and so
      confirmed.
    - ``payment_bank_transfer_recorded`` -- a bank transfer was recorded as
      pending.
    - ``payment_bank_transfer_confirmed`` -- a pending bank transfer was
      confirmed.
    - ``payment_bank_transfer_rejected`` -- a pending bank transfer was
      rejected; a reason is required.
    - ``payment_reversed`` -- a full reversing entry was recorded for one
      confirmed collection; a reason is required.
    - ``receipt_issued`` -- a confirmed collection received its receipt.
    - ``receipt_voided`` -- a reversed collection's receipt was voided; a
      reason is required.

    Verified online collections (Phase 5 / M07), both **system-origin**: the
    only kinds whose event has no acting Administrator, because a verified,
    signed provider webhook -- not a person -- caused them:

    - ``payment_online_confirmed`` -- a signed ``payment.succeeded`` webhook
      created a confirmed online collection.
    - ``receipt_online_issued`` -- that collection received its receipt.

    Visible deletion (Phase 5 / M10), each with a required reason: the row is
    kept as a tombstone, never physically deleted:

    - ``invoice_deleted`` -- a draft or issued invoice was deleted.
    - ``payment_deleted`` -- a payment transaction was deleted, alone or with
      its invoice's whole document family.
    - ``payment_replaced`` -- an edited manual collection was deleted and
      replaced by a new collection, which its snapshot names.
    - ``receipt_deleted`` -- a receipt was deleted with its collection.

    Refund, report and notification events belong to later Parts and have no
    member or placeholder here. Adding one is a schema change (the
    ``payment_audit_events.kind`` CHECK).
    """

    INVOICE_DRAFT_CREATED = "invoice_draft_created"
    INVOICE_DRAFT_EDITED = "invoice_draft_edited"
    INVOICE_ISSUED = "invoice_issued"
    INVOICE_ISSUED_EDITED = "invoice_issued_edited"
    INVOICE_CANCELLED = "invoice_cancelled"
    PAYMENT_CASH_RECORDED = "payment_cash_recorded"
    PAYMENT_BANK_TRANSFER_RECORDED = "payment_bank_transfer_recorded"
    PAYMENT_BANK_TRANSFER_CONFIRMED = "payment_bank_transfer_confirmed"
    PAYMENT_BANK_TRANSFER_REJECTED = "payment_bank_transfer_rejected"
    PAYMENT_REVERSED = "payment_reversed"
    RECEIPT_ISSUED = "receipt_issued"
    RECEIPT_VOIDED = "receipt_voided"
    PAYMENT_ONLINE_CONFIRMED = "payment_online_confirmed"
    RECEIPT_ONLINE_ISSUED = "receipt_online_issued"
    INVOICE_DELETED = "invoice_deleted"
    PAYMENT_DELETED = "payment_deleted"
    PAYMENT_REPLACED = "payment_replaced"
    RECEIPT_DELETED = "receipt_deleted"


class PaymentTransactionKind(str, enum.Enum):
    """What one :class:`~app.models.payment_transaction.PaymentTransaction`
    is (Phase 5 / M05).

    - ``collection`` -- money an Administrator recorded as received against
      one issued invoice, by cash or bank transfer.
    - ``reversal`` -- a full reversing entry for one confirmed collection. It
      cancels that collection's credit to the balance; it is an internal
      correction, **not** a refund and not evidence that money was returned.

    There is no ``refund``, ``credit_note``, ``discount`` or ``adjustment``
    member and no placeholder for one. Adding a member is a schema change
    (the ``payment_transactions.kind`` CHECK).
    """

    COLLECTION = "collection"
    REVERSAL = "reversal"


class PaymentMethod(str, enum.Enum):
    """How a :class:`~app.models.payment_transaction.PaymentTransaction`
    was paid (Phase 5 / M05).

    The two manual methods, and (Phase 5 / M07) ``online`` -- a collection
    created only by a verified, signed provider webhook for one payment
    intent. The member is generic: which provider collected it is read through
    the intent, never encoded in the method. A reversal copies its
    collection's method for historical classification only. There is no card,
    gateway or provider-specific member; adding one is a schema change (the
    ``payment_transactions.method`` CHECK).
    """

    CASH = "cash"
    BANK_TRANSFER = "bank_transfer"
    ONLINE = "online"


class PaymentTransactionStatus(str, enum.Enum):
    """Lifecycle of one
    :class:`~app.models.payment_transaction.PaymentTransaction`
    (Phase 5 / M05)::

        cash collection:  confirmed at creation
        bank collection:  pending -> confirmed
        bank collection:  pending -> rejected
        reversal:         confirmed at creation

    - ``pending`` -- a recorded bank transfer awaiting an Administrator's
      decision. It has no effect on the balance and reserves nothing.
    - ``confirmed`` -- counted in the balance. Terminal: a confirmed row never
      changes; a correction is a new ``reversal`` row.
    - ``rejected`` -- a bank transfer an Administrator rejected with a reason.
      Terminal, no financial effect, no receipt.
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class ReceiptStatus(str, enum.Enum):
    """Lifecycle of one :class:`~app.models.receipt.Receipt`
    (Phase 5 / M05).

    - ``issued`` -- the permanent operational receipt of one confirmed
      collection.
    - ``voided`` -- its collection was reversed. The receipt, its number and
      its issued document are kept as history; it is never deleted, restored,
      reissued or overwritten.

    ``issued -> voided`` is the only transition.
    """

    ISSUED = "issued"
    VOIDED = "voided"


class PaymentIntentStatus(str, enum.Enum):
    """Lifecycle of one :class:`~app.models.payment_intent.PaymentIntent`
    (Phase 5 / M06, extended by M07)::

        pending -> provider_succeeded | provider_failed | cancelled | confirmed
        provider_succeeded -> provider_failed | confirmed

    - ``pending`` -- created at the provider and awaiting its result. Active:
      it freezes its invoice's lines and cancellation, and (M07) refuses
      manual collection.
    - ``provider_succeeded`` -- the provider's success was *observed by the
      browser return*, read through its status operation. Active, and **not**
      a financial confirmation. It cannot be cancelled.
    - ``provider_failed`` -- the provider reported failure, by the browser
      return or by a verified signed webhook. Terminal.
    - ``cancelled`` -- an Administrator cancelled the pending intent after the
      provider confirmed the cancellation. Terminal.
    - ``confirmed`` (M07) -- a verified, signed ``payment.succeeded`` webhook
      created the online collection and its receipt. Terminal.

    There is no ``paid``, ``refunded`` or ``deleted`` member and no
    placeholder for one. Adding a member is a schema change (the
    ``payment_intents.status`` CHECK), which is the point.
    """

    PENDING = "pending"
    PROVIDER_SUCCEEDED = "provider_succeeded"
    PROVIDER_FAILED = "provider_failed"
    CANCELLED = "cancelled"
    CONFIRMED = "confirmed"


class ProviderEventType(str, enum.Enum):
    """The normalized type of one signed provider webhook event
    (Phase 5 / M07). Exactly the two a payment intent can receive; adding one
    is a schema change (the ``payment_provider_events.event_type`` CHECK)."""

    PAYMENT_SUCCEEDED = "payment.succeeded"
    PAYMENT_FAILED = "payment.failed"


class ProviderEventOutcome(str, enum.Enum):
    """What processing one verified provider event did (Phase 5 / M07).

    - ``confirmed`` -- a ``payment.succeeded`` event created the online
      collection, its receipt and its audit events, and confirmed the intent.
    - ``failed`` -- a ``payment.failed`` event moved an active intent to
      ``provider_failed``. No financial record.
    - ``duplicate`` -- a *new* event restating what the intent already records
      (a second success for a confirmed intent, a second failure for a failed
      one). No change. A re-delivery of the *same* event is not stored again.
    - ``ignored_terminal`` -- a failure for a cancelled intent: consistent, so
      nothing to do.
    - ``reconciliation_required`` -- the event conflicts with the recorded
      financial truth (a success for a cancelled or failed intent, a failure
      for a confirmed one, an amount or currency that differs from the
      intent, a manual payment, a changed balance, an invoice or assignment no
      longer collectable). Financially inert: an Administrator reconciles it
      outside the application.
    """

    CONFIRMED = "confirmed"
    FAILED = "failed"
    DUPLICATE = "duplicate"
    IGNORED_TERMINAL = "ignored_terminal"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class ResearchCollectionStatus(str, enum.Enum):
    """Whether one research subject is inside the natural-use collection
    population (Phase 6 replacement).

    - ``included`` -- collected while a configuration is live. **This is not a
      consent decision the Student took inside the application**, and nothing
      here claims one.
    - ``excluded`` -- collection must not happen for this Student, whatever
      the configuration says. Rechecked on every write, including a delayed
      batch, and never reversed by logging in or visiting a page.

    Adding a member is a schema change (the ``research_subjects`` CHECK).
    """

    INCLUDED = "included"
    EXCLUDED = "excluded"


class ResearchStatusBasis(str, enum.Enum):
    """The truthful provenance of a subject's collection status.

    - ``population_rule`` -- included automatically because the account is an
      eligible Student; the server provisioned the subject at its first
      collection write. Not an individual acceptance, and not an operator
      confirmation;
    - ``operator_reinstatement`` -- an operator lifted an earlier exclusion
      (audited);
    - ``external_exclusion`` -- excluded through the centre's external
      process, as recorded by an operator (audited);
    - ``legacy_collection_exclusion`` -- excluded by the replacement migration
      because the superseded in-app workflow held a refusal or a withdrawal
      for this Student. It preserves that exclusion; it never restores or
      implies an acceptance.
    """

    POPULATION_RULE = "population_rule"
    OPERATOR_REINSTATEMENT = "operator_reinstatement"
    EXTERNAL_EXCLUSION = "external_exclusion"
    LEGACY_COLLECTION_EXCLUSION = "legacy_collection_exclusion"


class ResearchProvenance(str, enum.Enum):
    """Server-controlled data provenance. A client can never set it.

    - ``study`` -- real research data. The only provenance ever exported.
    - ``demo`` -- a subject an operator marked as a demonstration account;
      everything collected for it is demo data.
    - ``development`` -- collected by an application not configured as a
      study deployment (``RESEARCH_DATA_PROVENANCE``).
    """

    STUDY = "study"
    DEMO = "demo"
    DEVELOPMENT = "development"


class ResearchConfigurationStatus(str, enum.Enum):
    """The lifecycle of one versioned collection configuration.

    ``draft`` may be edited by a Researcher; ``active`` is frozen and is the
    one configuration collection runs under (its operational collecting or
    paused state still changes); ``retired`` is frozen forever. At most one
    configuration is ``active``.
    """

    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class ResearchSessionEndReason(str, enum.Enum):
    """Why a natural-use session ended, when that is known."""

    LOGOUT = "logout"
    INACTIVITY = "inactivity"
    CONFIGURATION_CHANGED = "configuration_changed"
    COLLECTION_STOPPED = "collection_stopped"
    SUBJECT_INELIGIBLE = "subject_ineligible"


class ResearchPromptStatus(str, enum.Enum):
    """The stored state of one sampled feedback prompt.

    ``offered`` -- sampled, not yet shown; ``displayed`` -- shown, not yet
    answered; ``answered`` -- a 1-5 rating was given; ``dismissed`` -- the
    Student chose Skip. An offer that is never shown and a display that is
    never answered are *derived* from the moments and the configuration's
    lifetimes, never stored as a guess.
    """

    OFFERED = "offered"
    DISPLAYED = "displayed"
    ANSWERED = "answered"
    DISMISSED = "dismissed"


class ResearchSamplingReason(str, enum.Enum):
    """Why a prompt was offered: a random eligible moment, or a natural
    activity ending. Never a suspicion of frustration."""

    RANDOM = "random"
    ACTIVITY_END = "activity_end"


class ResearchDeferralReason(str, enum.Enum):
    """Why the browser deferred showing an offered prompt."""

    TIMED_ACTIVITY = "timed_activity"
    RECORDING = "recording"
    UPLOADING = "uploading"
    HIDDEN_TAB = "hidden_tab"


class ResearchAuditAction(str, enum.Enum):
    """One audited research administration action. No Student content."""

    CONFIGURATION_CREATED = "configuration_created"
    CONFIGURATION_UPDATED = "configuration_updated"
    CONFIGURATION_ACTIVATED = "configuration_activated"
    COLLECTION_PAUSED = "collection_paused"
    COLLECTION_RESUMED = "collection_resumed"
    SUBJECT_EXCLUDED = "subject_excluded"
    SUBJECT_REINSTATED = "subject_reinstated"
    SUBJECT_MARKED_DEMO = "subject_marked_demo"
    EXPORT_CREATED = "export_created"
    EXPORT_DOWNLOADED = "export_downloaded"
    RETENTION_PURGED = "retention_purged"


class ResearchAuditChannel(str, enum.Enum):
    """Where an audited action came from."""

    WORKSPACE = "workspace"
    OPERATOR = "operator"
    MIGRATION = "migration"
