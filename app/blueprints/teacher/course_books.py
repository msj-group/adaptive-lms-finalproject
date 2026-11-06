"""Read teaching books only through a current assignment to the actual Group."""
from flask import url_for

from app.blueprints.teacher import teacher_bp
from app.blueprints.teacher.units import _teacher_group_or_404
from app.security.decorators import roles_required
from app.services.course_books import (BOOK_LABELS, book_page, books_for_course,
                                      download_book, private_render, read_book)


def _urls(group):
    return lambda action, **values: url_for("teacher.course_book_" + action, group_public_id=group.public_id, **values)


@teacher_bp.get("/groups/<group_public_id>/books")
@roles_required("teacher")
def course_book_list(group_public_id):
    from app.services.teaching_assets import asset_urls, assets_for_course
    group = _teacher_group_or_404(group_public_id)
    return private_render("teacher/course_books.html", group=group, course=group.course,
                          books=books_for_course(group.course_id, group), book_labels=BOOK_LABELS,
                          teaching_assets=assets_for_course(group.course_id), resource_urls=asset_urls(group.course, group),
                          book_urls=_urls(group))


@teacher_bp.get("/groups/<group_public_id>/books/<kind>")
@roles_required("teacher")
def course_book_read(group_public_id, kind):
    group = _teacher_group_or_404(group_public_id)
    return read_book(group.course, kind, _urls(group), group)


@teacher_bp.get("/groups/<group_public_id>/books/<kind>/<file_id>/pages/<int:page_number>")
@roles_required("teacher")
def course_book_page(group_public_id, kind, file_id, page_number):
    group = _teacher_group_or_404(group_public_id)
    return book_page(group.course_id, kind, file_id, page_number, group)


@teacher_bp.get("/groups/<group_public_id>/books/<kind>/<file_id>/download")
@roles_required("teacher")
def course_book_download(group_public_id, kind, file_id):
    group = _teacher_group_or_404(group_public_id)
    return download_book(group.course_id, kind, file_id, group)


@teacher_bp.get("/groups/<group_public_id>/book-media/<material_public_id>")
@roles_required("teacher")
def course_book_material_file(group_public_id, material_public_id):
    from app.services.course_books import main_material_file
    group = _teacher_group_or_404(group_public_id)
    return main_material_file(group.course_id, material_public_id, group)


@teacher_bp.get("/groups/<group_public_id>/resources/<material_public_id>")
@roles_required("teacher")
def teaching_asset_read(group_public_id, material_public_id):
    from app.services.teaching_assets import read_asset
    group = _teacher_group_or_404(group_public_id)
    return read_asset(group.course, material_public_id, group)


@teacher_bp.get("/groups/<group_public_id>/resources/<material_public_id>/file")
@roles_required("teacher")
def teaching_asset_file(group_public_id, material_public_id):
    from app.services.teaching_assets import asset_file
    group = _teacher_group_or_404(group_public_id)
    return asset_file(group.course_id, material_public_id)


@teacher_bp.get("/groups/<group_public_id>/resources/<material_public_id>/download")
@roles_required("teacher")
def teaching_asset_download(group_public_id, material_public_id):
    from app.services.teaching_assets import asset_file
    group = _teacher_group_or_404(group_public_id)
    return asset_file(group.course_id, material_public_id, download=True)


@teacher_bp.get("/groups/<group_public_id>/resources/<material_public_id>/<file_id>/pages/<int:page_number>")
@roles_required("teacher")
def teaching_asset_page(group_public_id, material_public_id, file_id, page_number):
    from app.services.teaching_assets import asset_page
    group = _teacher_group_or_404(group_public_id)
    return asset_page(group.course_id, material_public_id, file_id, page_number)
