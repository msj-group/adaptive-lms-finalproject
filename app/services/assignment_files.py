"""Bounded private Assignment file reads and resolved upload presentation rules.

Storage/validation/serving stay in the existing shared private-file pipeline.
Callers prove Group/Assignment access first; these reads prove payload ownership.
No research metadata is produced here.
"""
from app.extensions import db
from app.models import Submission, UploadedFile, User, UserRole
from app.services.episode_queries import active_episode_record
from app.services.material_config import current_material_config


def upload_policy_view():
    config = current_material_config()
    extensions = sorted(config.allowed_extensions)
    return {
        "accept": ",".join("." + ext for ext in extensions),
        "extensions": ", ".join(ext.upper() for ext in extensions),
        "limits": [(category, format(config.max_bytes_for_category(category) / (1024 * 1024), ".3g")) for category in sorted(config.enabled_categories)],
    }


def file_metadata(file_id, student_id):
    if file_id is None:
        return None
    row = db.session.query(UploadedFile.original_filename, UploadedFile.extension, UploadedFile.byte_size).filter(UploadedFile.id == file_id, UploadedFile.uploaded_by_id == student_id).first()
    if row is None:
        raise RuntimeError("Submission file ownership integrity error")
    return {"name": row.original_filename, "extension": row.extension, "byte_size": row.byte_size}


def submission_uploaded_file(assignment_id, submission_public_id, *, student_id=None):
    query = UploadedFile.query.join(Submission, Submission.uploaded_file_id == UploadedFile.id).join(User, Submission.student_id == User.id).filter(
        Submission.assignment_id == assignment_id,
        Submission.public_id == submission_public_id,
        User.role == UserRole.STUDENT.value,
        UploadedFile.uploaded_by_id == Submission.student_id,
    )
    if student_id is not None:
        query = query.filter(Submission.student_id == student_id, active_episode_record(Submission))
    return query.first_or_404()
