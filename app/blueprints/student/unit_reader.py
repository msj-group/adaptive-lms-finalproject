"""Compose one continuous Unit using the existing own-Lesson/file guards."""
from flask import abort, current_app
from app.models.teaching_asset import TeachingAssetPin

from app.services.student_lessons import outline_units, student_lesson_detail
from app.services.unit_progress_tokens import make_unit_progress_token


def unit_reader_context(student_id, group_public_id, unit_public_id, lesson_public_id, book='main', *, actor_public_id):
    primary = student_lesson_detail(student_id, group_public_id, unit_public_id, lesson_public_id, book=book)
    if primary is None:
        abort(404)
    if book == 'workbook':
        from app.blueprints.student.books import workbook_context, _workbook_media
        group, workbook_document, outline, mappings, _ = workbook_context(student_id, group_public_id)
        if not mappings.get(lesson_public_id):
            abort(404)
    else:
        outline = outline_units(primary.progress.group_id, student_id)
    unit = next((item for item in outline if item['public_id'] == unit_public_id), None)
    if unit is None:
        abort(404)
    pages = [primary if item['public_id'] == lesson_public_id else
             student_lesson_detail(student_id, group_public_id, unit_public_id, item['public_id'], book=book)
             for item in unit['lessons']]
    if any(page is None for page in pages):
        abort(404)  # Publication changed during composition; never broaden the page.
    numbers, owners, materials, pins = [], {}, [], {}
    document = primary.view['course_document']
    entry_page = primary.view['page_start'] if document else None
    entry_material = next((item['public_id'] for item in primary.view['materials']), None)
    for page in pages:
        lesson_id = page.progress.lesson_public_id
        args = dict(group_public_id=group_public_id, unit_public_id=unit_public_id, lesson_public_id=lesson_id)
        if page.view['course_document'] and page.view['page_start'] is not None:
            document = page.view['course_document']
            for number in range(page.view['page_start'], page.view['page_end'] + 1):
                if number not in owners:
                    numbers.append(number)
                    owners[number] = args
        for source in page.view['materials']:
            material = dict(source, reader_media_args=args, reader_lesson_public_id=lesson_id)
            materials.append(material)
            if material['origin'] == 'course' and material.get('pin_page') is not None:
                pins.setdefault(material['pin_page'], []).append(material)
    reader_outline = outline
    if book == 'workbook':
        document = workbook_document
        mapped_unit = next((item for item in reader_outline if item['public_id'] == unit_public_id), None)
        if mapped_unit is None or not mappings.get(lesson_public_id):
            abort(404)
        entry_page = mappings[lesson_public_id][0]
        entry_material = None
        numbers = sorted({number for item in mapped_unit['lessons'] for number in mappings[item['public_id']]})
        owners, materials, pins = {}, [], {}
        for pin, asset in _workbook_media(group.course_id, numbers).order_by(TeachingAssetPin.id):
            pins.setdefault(pin.page_number, []).append(dict(public_id=asset.public_id,
                category=asset.uploaded_file.category, original_filename=asset.title,
                anchor_id=f'{asset.public_id}-{pin.page_number}-{pin.pin_x}-{pin.pin_y}',
                pin_x=pin.pin_x, pin_y=pin.pin_y))
    index = next(i for i, item in enumerate(reader_outline) if item['public_id'] == unit_public_id)
    next_unit = reader_outline[index + 1] if index + 1 < len(reader_outline) else None
    next_link = dict(unit_public_id=next_unit['public_id'], public_id=next_unit['lessons'][0]['public_id']) if next_unit else None
    view = dict(primary.view, course_document=document, materials=materials,
                page_start=numbers[0] if numbers else None, page_end=numbers[-1] if numbers else None)
    versions = {page.progress.lesson_public_id: page.progress.version for page in pages}
    return primary, dict(view, reader_book=book,
        reader_lesson_endpoint='student.workbook_lesson' if book == 'workbook' else 'student.lesson_detail',
        lesson_units=reader_outline, unit_page_numbers=numbers, unit_page_args=owners, unit_page_pins=pins,
        reader_entry_page=entry_page, reader_entry_material=entry_material,
        next_unit=next_link, unit_complete=all(page.progress.completed_at is not None for page in pages),
        unit_progress_state=make_unit_progress_token(actor_public_id, group_public_id, unit_public_id, book, versions),
        tz_name=current_app.config.get('APP_TIMEZONE', 'UTC'))
