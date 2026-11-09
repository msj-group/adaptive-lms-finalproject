"""Server-owned route, authorization and ownership scope for both editors."""
from flask import abort, url_for
from flask_login import current_user

from app.extensions import db
from app.models import Course, Unit
from app.models.course_document import CourseDocument
from app.models.group_curriculum import GroupCurriculum
from app.services.course_pdf import DOCUMENT_MAX_BYTES


class EditorScope:
    def __init__(self, course, group=None):
        self.course = course
        self.course_id = course.id
        self.group = group
        self.group_id = group.id if group else None
        self.public_id = group.public_id if group else course.public_id
        self.group_public_id = group.public_id if group else None
        self.term_id = group.academic_term_id if group else None
        self.level_id = course.level_id

    @property
    def route_values(self):
        return {"group_public_id" if self.group_id else "public_id": self.public_id}

    def endpoint(self, action):
        return ("teacher" if self.group_id else "admin") + ".curriculum_" + action

    def url(self, action, **values):
        if action == "edit" and not self.group_id:
            return url_for(self.endpoint(action), kind="material", **self.route_values, **values)
        return url_for(self.endpoint(action), **self.route_values, **values)

    @property
    def unit_scope(self):
        return Unit.group_id == self.group_id if self.group_id else Unit.course_id == self.course_id

    def document(self):
        row = (db.session.get(GroupCurriculum, self.group_id) if self.group_id
               else db.session.get(CourseDocument, self.course_id))
        return row if row and row.uploaded_file_id else None

    def save_document(self, uploaded, page_count):
        row = (db.session.get(GroupCurriculum, self.group_id) if self.group_id
               else db.session.get(CourseDocument, self.course_id))
        if row is None:
            row = CourseDocument(course_id=self.course_id, uploaded_file=uploaded, page_count=page_count)
            db.session.add(row)
        else:
            row.uploaded_file = uploaded
            row.page_count = page_count
            row.revision += 1

    def lock(self):
        if not self.group_id:
            from app.blueprints.admin.curriculum import _lock_course
            _lock_course(self.course_id, current_user.id)
            return
        from app.blueprints.teacher.units import _authz_broken, _hierarchy_active_error, _lock_unit_chain

        hierarchy, group, teacher, assignment, _ = _lock_unit_chain(
            self.group_public_id, self.term_id, self.level_id, self.course_id, current_user.id
        )
        if _authz_broken(group, teacher, assignment):
            db.session.rollback()
            abort(404)
        error = _hierarchy_active_error(hierarchy, group, self.term_id, self.level_id, self.course_id)
        if error:
            raise ValueError(error)
        copy = db.session.get(GroupCurriculum, self.group_id)
        if copy is None or copy.course_id != group.course_id:
            raise ValueError("Open the group workspace to prepare its curriculum copy first.")

    def context(self):
        return dict(
            book_upload_limit=DOCUMENT_MAX_BYTES,
            curriculum_url=self.url, editor_is_group=bool(self.group_id),
            editor_context_name=self.group.name if self.group_id else self.course.title,
            editor_back_url=(url_for("workspace.teacher_group", group_public_id=self.group_public_id)
                             if self.group_id else url_for("admin.course_book_list", public_id=self.public_id)),
            editor_back_label="Back to group" if self.group_id else "Back to course content",
            editor_file_endpoint=self.endpoint("file"), editor_page_endpoint=self.endpoint("document_page"),
            editor_route_values=self.route_values,
        )


def admin_editor(public_id):
    return EditorScope(Course.query.filter_by(public_id=public_id).first_or_404())


def teacher_editor(group_public_id):
    from app.blueprints.teacher.units import _teacher_group_or_404

    group = _teacher_group_or_404(group_public_id)
    copy = db.session.get(GroupCurriculum, group.id)
    if copy is None or copy.course_id != group.course_id:
        abort(404)
    return EditorScope(group.course, group)
