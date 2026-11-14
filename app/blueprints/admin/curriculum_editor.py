"""Administrator routes for the shared course editor."""
from app.blueprints.admin import admin_bp
from app.security.decorators import roles_required
from app.services import curriculum_editor as editor
from app.services.curriculum_editor_scope import admin_editor


@admin_bp.route("/courses/<public_id>/curriculum/division", methods=['GET', 'POST'])
@roles_required("administrator")
def curriculum_division(public_id):
    return editor.division(admin_editor(public_id))


@admin_bp.route("/courses/<public_id>/curriculum/document", methods=['POST'])
@roles_required("administrator")
def curriculum_document_upload(public_id):
    return editor.document_upload(admin_editor(public_id))


@admin_bp.route("/courses/<public_id>/curriculum/document/<document_id>/pages/<int:page_number>", methods=['GET'])
@roles_required("administrator")
def curriculum_document_page(public_id, document_id, page_number):
    return editor.document_page(admin_editor(public_id), document_id, page_number)


@admin_bp.route("/courses/<public_id>/curriculum/content", methods=['GET'])
@roles_required("administrator")
def curriculum_content(public_id):
    return editor.content(admin_editor(public_id))


@admin_bp.route("/courses/<public_id>/curriculum/content/<lesson_id>/media", methods=['POST'])
@roles_required("administrator")
def curriculum_media_save(public_id, lesson_id):
    return editor.media_save(admin_editor(public_id), lesson_id)
