"""Correct a finalized attendance mark while preserving its roster and revisions."""
from app.extensions import db
from app.models import AttendanceRecord, AttendanceSession, Group, User
from app.models.submission_feedback import whole_second_utc
from app.services.actor_authorization import require_current_actor
from app.services.attendance_transactions import lock_session_chain


def attendance_snapshot(record):
    return {"public_id": record.public_id, "version": record.version,
            "status": record.status, "note": record.note}


def correct_finalized_attendance(group_public_id, session_public_id, record_public_id,
                                 actor_id, expected, status, note):
    if status not in {"present", "absent", "late", "excused"} or not isinstance(note, str) or len(note) > 1000:
        raise ValueError("Enter a valid attendance mark and a private note of at most 1000 characters.")
    preview = db.session.query(Group, AttendanceSession, AttendanceRecord).join(
        AttendanceSession, AttendanceSession.group_id == Group.id).join(
        AttendanceRecord, AttendanceRecord.attendance_session_id == AttendanceSession.id).join(
        User, User.id == AttendanceRecord.student_id).filter(
        Group.public_id == group_public_id, AttendanceSession.public_id == session_public_id,
        AttendanceRecord.public_id == record_public_id, User.role == "student").first()
    if preview is None:
        raise ValueError("This attendance record is no longer available.")
    group, session, record = preview
    group_id, term_id, course_id, level_id = group.id, group.academic_term_id, group.course_id, group.course.level_id
    session_id, schedule_id, record_id, student_id = session.id, session.schedule_id, record.id, record.student_id
    locks = lock_session_chain(group_public_id, term_id, level_id, course_id, actor_id,
                               schedule_id, session_id, [student_id], [record_id])
    require_current_actor(actor_id, "administrator")
    group, session, record = locks.group, locks.session, locks.records.get(record_id)
    student = locks.students.get(student_id)
    if (group is None or group.id != group_id or group.academic_term_id != term_id or group.course_id != course_id
            or locks.hierarchy.term(term_id) is None or locks.hierarchy.level(level_id) is None
            or locks.hierarchy.course(course_id) is None or locks.hierarchy.course(course_id).level_id != level_id
            or locks.schedule is None or locks.schedule.group_id != group_id
            or session is None or session.public_id != session_public_id or session.group_id != group_id
            or session.schedule_id != schedule_id or record is None or record.public_id != record_public_id
            or record.attendance_session_id != session_id or record.student_id != student_id
            or student is None or student.role != "student"):
        raise ValueError("The attendance context changed. Reload and review it.")
    if session.finalized_at is None:
        raise ValueError("Draft attendance must be recorded by the group's teacher.")
    if attendance_snapshot(record) != expected:
        raise ValueError("This mark changed since you opened the form. Reload and review it.")
    note = note.strip() or None
    if record.status == status and record.note == note:
        return False
    record.status, record.note = status, note
    record.version += 1
    record.updated_at = whole_second_utc()
    # The shared mapper hook appends the complete old/new academic revision
    # in this same transaction, attributed to the authenticated Administrator.
    db.session.flush()
    return True
