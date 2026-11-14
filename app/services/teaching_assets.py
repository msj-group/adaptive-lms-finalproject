"""Private resources: callers must authorize Administrator or assigned Teacher."""
from flask import abort, redirect, request, url_for
from flask_login import current_user
from sqlalchemy.orm import joinedload

from app.models.teaching_asset import TeachingAsset, TeachingAssetPin
from app.services.material_serving import serve_uploaded_file
from app.services.course_pdf import serve_pdf_page


def asset_urls(course, group=None, asset=None):
    prefix = "teacher" if group else "admin"
    scope = {"group_public_id": group.public_id} if group else {"public_id": course.public_id}
    def urls(action, **values):
        if action == "list":
            return url_for(prefix + ".course_book_list", **scope)
        identity = values.pop("material_public_id", None) or asset.public_id
        values.pop("kind", None)
        if action != "page":
            values.pop("file_id", None)
        return url_for(prefix + ".teaching_asset_" + action, material_public_id=identity, **scope, **values)
    return urls


def assets_for_course(course_id):
    return (TeachingAsset.query.options(joinedload(TeachingAsset.uploaded_file))
            .filter_by(course_id=course_id).order_by(TeachingAsset.display_order, TeachingAsset.id).all())


def asset_or_404(course_id, public_id):
    return (TeachingAsset.query.options(joinedload(TeachingAsset.uploaded_file))
            .filter_by(course_id=course_id, public_id=public_id).first_or_404())


def reference_pins(course_id, page=None, *, book_kind=None, document_asset=None):
    query = (TeachingAssetPin.query.join(TeachingAsset,
        (TeachingAsset.id == TeachingAssetPin.asset_id) & (TeachingAsset.course_id == TeachingAssetPin.course_id))
        .filter(TeachingAssetPin.course_id == course_id))
    if page is not None:
        query = query.filter(TeachingAssetPin.page_number == page)
    if book_kind is not None:
        query = query.filter(TeachingAssetPin.book_kind == book_kind, TeachingAssetPin.document_asset_id.is_(None))
    elif document_asset is not None and document_asset.course_id == course_id:
        query = query.filter(TeachingAssetPin.document_asset_id == document_asset.id, TeachingAssetPin.book_kind.is_(None))
    else:
        abort(404)
    rows = query.add_entity(TeachingAsset).options(joinedload(TeachingAsset.uploaded_file)).all()
    result = []
    for pin, asset in rows:
        uploaded = asset.uploaded_file
        if uploaded.category not in {"audio", "video"}:
            continue
        result.append(dict(public_id=asset.public_id, category=uploaded.category, pin_page=pin.page_number,
            anchor_id=f"{asset.public_id}-{pin.page_number}-{pin.pin_x}-{pin.pin_y}",
            original_filename=asset.title, pin_x=pin.pin_x, pin_y=pin.pin_y))
    return result


def read_asset(course, public_id, group=None):
    from app.services.course_books import private_render
    asset = asset_or_404(course.id, public_id)
    urls = asset_urls(course, group, asset)
    if asset.uploaded_file.extension == "pdf" and asset.page_count:
        page = request.args.get("page", 1, type=int)
        if page is None or not 1 <= page <= asset.page_count:
            abort(404)
        return private_render("curriculum/book_reader.html", course=course, group=group,
            book=asset, kind="resource", label=asset.title, page=page, book_urls=urls,
            page_pins=reference_pins(course.id, page, document_asset=asset),
            pin_endpoint=("teacher" if group else "admin") + ".teaching_asset_file",
            pin_args={"group_public_id": group.public_id} if group else {"public_id": course.public_id},
            reader_layout="layouts/portal_base.html" if group else "layouts/admin_base.html")
    if asset.uploaded_file.category not in {"audio", "video"}:
        return redirect(urls("download"))
    return private_render("curriculum/teaching_asset_reader.html", course=course, group=group,
        asset=asset, resource_urls=urls,
        reader_layout="layouts/portal_base.html" if group else "layouts/admin_base.html")


def asset_page(course_id, public_id, file_id, page_number):
    asset = asset_or_404(course_id, public_id)
    if asset.uploaded_file.extension != "pdf" or not asset.page_count or asset.uploaded_file.public_id != file_id:
        abort(404)
    return serve_pdf_page(asset, page_number)


def asset_file(course_id, public_id, *, download=False):
    asset = asset_or_404(course_id, public_id)
    return serve_uploaded_file(asset.uploaded_file, current_user.id, download, course_captions=True)
