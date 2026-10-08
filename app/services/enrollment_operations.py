"""Atomic registration, withdrawal, transfer and wrong-course correction.

Lock order: academic ancestors, Groups, Users sorted, episode/memberships,
Invoices, Payments/Receipts, number sequences. No function commits halfway.
"""
import hashlib
import json
import uuid
from app.extensions import db
from app.models import Course, Enrollment, EnrollmentEvent, EnrollmentMembership, Group, GroupTeacherAssignment, Invoice, User
from app.models.submission_feedback import whole_second_utc
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.financial_history import document_snapshot
from app.services.general_finance import FinanceConflict, correct_invoice, issue_enrollment_invoice, record_money
from app.services.group_memberships import active_student_enrollment_count, conflicting_active_enrollment, eligible_active_teacher_count


class EnrollmentConflict(ValueError):
    pass


def enrollment_preview(group, course, episode=None, invoice=None):
    return {"group": {key: value for key, value in document_snapshot(group).items() if key != "updated_at"},
            "course": {key: value for key, value in document_snapshot(course).items() if key != "updated_at"},
            "episode": document_snapshot(episode) if episode else None,
            "invoice": document_snapshot(invoice) if invoice else None}


def _episode_invoice(episode):
    return Invoice.query.filter_by(enrollment_id=episode.id).filter(Invoice.deleted_at.is_(None), Invoice.status == "issued").order_by(Invoice.id).populate_existing().with_for_update().first()


def _lock_context(source_public_id, target_public_id, student_public_id, episode_public_id=None):
    """Discovery is discarded; all conditional facts come from current locks."""
    public_ids = {value for value in [source_public_id, target_public_id] if value}
    student_id = db.session.query(User.id).filter_by(public_id=student_public_id).scalar()
    if student_id is None:
        raise EnrollmentConflict("The selected Student account is unavailable.")
    previews = Group.query.filter(Group.public_id.in_(public_ids)).all()
    group_specs = {row.public_id: (row.id, row.academic_term_id, row.course_id) for row in previews}
    course_specs = {row.id: row.level_id for row in Course.query.filter(Course.id.in_([spec[2] for spec in group_specs.values()])).all()}
    if set(group_specs) != public_ids:
        raise EnrollmentConflict("The selected group is unavailable.")
    hierarchy = lock_academic_hierarchy(term_ids=[spec[1] for spec in group_specs.values()],
        course_ids=course_specs, level_ids=course_specs.values())
    groups = {}
    for public_id, spec in sorted(group_specs.items(), key=lambda item: item[1][0]):
        row = Group.query.filter_by(id=spec[0]).populate_existing().with_for_update().first()
        if (row is None or (row.academic_term_id, row.course_id) != spec[1:]
                or hierarchy.course(row.course_id) is None
                or hierarchy.course(row.course_id).level_id != course_specs[row.course_id]):
            raise EnrollmentConflict("The academic context changed. Reload before continuing.")
        groups[public_id] = row
    # Group owners prevent concurrent membership inserts while this lock read
    # discovers Teachers. Lock all involved Users before any ordinary SELECT.
    teacher_ids = [row[0] for row in db.session.query(GroupTeacherAssignment.teacher_id).filter(
        GroupTeacherAssignment.group_id.in_([row.id for row in groups.values()]), GroupTeacherAssignment.status == "active").with_for_update().all()]
    users = {row.id: row for row in User.query.filter(User.id.in_(set(teacher_ids + [student_id]))).order_by(User.id).populate_existing().with_for_update().all()}
    student = users.get(student_id)
    if student is None or student.role != "student":
        raise EnrollmentConflict("The selected Student account is unavailable.")
    episode = None
    if episode_public_id:
        episode = Enrollment.query.filter_by(public_id=episode_public_id, student_id=student.id).populate_existing().with_for_update().first()
        if episode is None:
            raise EnrollmentConflict("This enrollment is unavailable.")
    return hierarchy, groups, student, episode


def study_has_started(group, *, at=None):
    """Informational only: study start does not close enrollment."""
    if group is None or group.study_starts_at is None:
        return False
    moment = whole_second_utc() if at is None else at
    return moment >= group.study_starts_at


def _target_error(hierarchy, group, student, *, excluding=None):
    course = hierarchy.course(group.course_id)
    term = hierarchy.term(group.academic_term_id)
    level = hierarchy.level(course.level_id)
    if student.status != "active" or any(row.status != "active" for row in (group, course, term, level)):
        return "The Student and destination academic context must all be active."
    if group.study_starts_at is None:
        return "Set this group's study start before changing enrollment."
    if eligible_active_teacher_count(group.id) == 0:
        return "Assign an active Teacher before enrolling or transferring students."
    if active_student_enrollment_count(group.id) >= group.capacity:
        return "The destination group has reached capacity."
    existing = Enrollment.query.filter_by(student_id=student.id, group_id=group.id, status="active").first()
    if existing is not None and existing.id != excluding:
        return "This Student already has an active enrollment in the destination."
    conflict = conflicting_active_enrollment(student.id, group.id)
    if conflict is not None and conflict.id != excluding:
        return "Another active enrollment already covers this course and term."
    return None


def _close_membership(episode, moment):
    membership = EnrollmentMembership.query.filter_by(enrollment_id=episode.id, active_marker=1).populate_existing().with_for_update().first()
    if membership is None or membership.group_id != episode.group_id:
        raise EnrollmentConflict("This episode's membership history is inconsistent. No change was saved.")
    membership.active_marker, membership.left_at = None, moment


def _new_episode(student, group, moment):
    episode = Enrollment(student_id=student.id, group_id=group.id, status="active", active_marker=1,
                         version=1, created_at=moment, updated_at=moment)
    db.session.add(episode)
    db.session.flush()
    db.session.add(EnrollmentMembership(enrollment_id=episode.id, group_id=group.id, active_marker=1, joined_at=moment))
    return episode


def change_enrollment(*, action, actor_id, student_public_id, source_public_id=None, target_public_id=None,
                      episode_public_id=None, expected_source=None, expected_target=None, operation_key,
                      discount_kind="none", discount_value="0", discount_reason=None, initial_amount=None,
                      initial_method="cash", initial_bank_reference=None, initial_bank_date=None,
                      initial_confirmed=False, cancel_obligation=False):
    if action not in {"enroll", "withdraw", "transfer", "correct_course"}:
        raise EnrollmentConflict("Select a supported enrollment action.")
    try:
        if str(uuid.UUID(operation_key)) != operation_key:
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise EnrollmentConflict("Reload the form before submitting.") from None
    # Plain immutable discovery, retained only as an id; no User lock precedes
    # academic ancestors. All other preview reads are reset by _lock_context.
    hierarchy, groups, student, episode = _lock_context(source_public_id, target_public_id, student_public_id, episode_public_id)
    from app.services.actor_authorization import require_current_actor
    actor = require_current_actor(actor_id, "administrator")
    fingerprint = hashlib.sha256(json.dumps({"action": action, "student": student_public_id, "source": source_public_id,
        "target": target_public_id, "episode": episode_public_id, "source_state": expected_source, "target_state": expected_target,
        "discount": [discount_kind, discount_value, discount_reason], "initial": [initial_amount, initial_method, initial_bank_reference,
        str(initial_bank_date or ""), bool(initial_confirmed)], "cancel_obligation": bool(cancel_obligation)}, sort_keys=True).encode()).hexdigest()
    replay = EnrollmentEvent.query.filter_by(operation_key=operation_key).first()
    if replay is not None:
        if replay.after_snapshot.get("request_fingerprint") != fingerprint:
            raise EnrollmentConflict("This submission was already used for another enrollment change.")
        return db.session.get(Enrollment, replay.enrollment_id)
    source = groups.get(source_public_id)
    target = groups.get(target_public_id)
    invoice = _episode_invoice(episode) if episode else None
    if episode is not None and episode.group_id != source.id:
        raise EnrollmentConflict("This enrollment no longer belongs to the selected group.")
    if source is not None and expected_source != enrollment_preview(source, hierarchy.course(source.course_id), episode, invoice):
        raise EnrollmentConflict("The enrollment or invoice changed. Review the fresh preview.")
    if target is not None and expected_target != enrollment_preview(target, hierarchy.course(target.course_id)):
        raise EnrollmentConflict("The destination or price changed. Review the fresh preview.")
    if action != "enroll" and (episode is None or episode.status != "active"):
        raise EnrollmentConflict("Only an active enrollment can be withdrawn, transferred or corrected.")
    if action == "transfer" and (source.id == target.id or source.course_id != target.course_id):
        raise EnrollmentConflict("Transfers require a different group of the same course.")
    if action == "correct_course" and source.course_id == target.course_id:
        raise EnrollmentConflict("Use Transfer for another group of the same course.")
    if target is not None:
        error = _target_error(hierarchy, target, student, excluding=episode.id if episode else None)
        if error:
            raise EnrollmentConflict(error)
    moment = whole_second_utc()
    before = {"episode": document_snapshot(episode),
        "group": document_snapshot(source), "course": document_snapshot(hierarchy.course(source.course_id)),
        "invoice": document_snapshot(invoice) if invoice else None} if episode else None
    previous_invoice = invoice
    if action in {"withdraw", "transfer", "correct_course"}:
        _close_membership(episode, moment)
        episode.version += 1
        episode.updated_at = moment
        if action == "transfer":
            # Close the old membership before opening the next unique marker.
            db.session.flush()
            episode.group_id = target.id
            db.session.add(EnrollmentMembership(enrollment_id=episode.id, group_id=target.id, active_marker=1, joined_at=moment))
        else:
            episode.status, episode.active_marker, episode.withdrawn_at = "withdrawn", None, moment
            if invoice is not None and (cancel_obligation or action == "correct_course"):
                correct_invoice(invoice, student, actor_id, invoice.version, None, "none", "0", "Enrollment obligation cancelled", reverse=True)
    previous_episode = episode
    initial_collection = None
    if action in {"enroll", "correct_course"}:
        episode = _new_episode(student, target, moment)
        invoice = issue_enrollment_invoice(student, episode, hierarchy.course(target.course_id), actor_id,
            discount_kind, discount_value, discount_reason)
        if initial_amount:
            initial_collection = record_money(student, actor_id, initial_amount, initial_method, "in", operation_key, invoice=invoice,
                confirmed=initial_confirmed, bank_reference=initial_bank_reference, bank_date=initial_bank_date)
    after = {"episode": document_snapshot(episode), "request_fingerprint": fingerprint,
             "previous_episode": document_snapshot(previous_episode) if previous_episode and previous_episode.id != episode.id else None,
             "invoice_public_id": invoice.public_id if invoice else None,
             "group": document_snapshot(target or source),
             "course": document_snapshot(hierarchy.course((target or source).course_id)),
             "invoice": document_snapshot(invoice) if invoice else None,
             "previous_invoice": document_snapshot(previous_invoice) if previous_invoice and previous_invoice != invoice else None,
             "initial_collection": document_snapshot(initial_collection) if initial_collection else None,
             "cancel_obligation": bool(cancel_obligation or action == "correct_course")}
    db.session.add(EnrollmentEvent(enrollment_id=episode.id, previous_enrollment_id=previous_episode.id if previous_episode and previous_episode.id != episode.id else None,
        actor_id=actor_id, action=action,
        operation_key=operation_key, before_snapshot=before, after_snapshot=after, created_at=moment))
    db.session.flush()
    return episode
