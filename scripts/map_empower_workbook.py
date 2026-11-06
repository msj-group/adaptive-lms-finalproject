"""Guarded, additive map for the reviewed Empower Intermediate Workbook.

No file copying or publication changes. Requires the exact full-book digest.
PDF pages 4..63 are exercises; 64..71 are Vox Pop exercises. Audio scripts,
answers and front matter have no Student mapping. Group copies are resolved
through existing Material lineage, never by guessed names or numeric ids.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from app.extensions import db
from app.models import Course, CourseReferenceBook, Group, GroupCurriculum, Lesson, Material, Unit, User, WorkbookLessonPage
from app.models.teaching_asset import GroupMaterialSource, TeachingAsset, TeachingAssetPin
from app.blueprints.admin.curriculum import _lock_course
from app.services.file_storage import open_stored_file
from app.services.material_config import current_material_config
from sqlalchemy.orm import aliased


def map_workbook(course_public_id, book_sha256, apply=False):
    course = Course.query.filter_by(public_id=course_public_id, code="C1", status="active").one()
    course_id = course.id
    admin_id = User.query.filter_by(role="administrator", status="active").one().id
    _lock_course(course_id, admin_id)
    book = CourseReferenceBook.query.filter_by(course_id=course_id, kind="workbook").with_for_update().one()
    assert book.page_count == 87 and book.uploaded_file.sha256 == book_sha256, "Unexpected Workbook"
    path = open_stored_file(current_material_config(), book.uploaded_file.storage_key)
    with path.open("rb") as source:
        assert hashlib.file_digest(source, "sha256").hexdigest() == book_sha256, "Workbook bytes changed"
    lessons = (Lesson.query.join(Unit).filter(Unit.course_id == course_id).all())
    by_code = {}
    for number in range(1, 11):
        for letter in "ABCD":
            matches = [lesson for lesson in lessons if re.match(rf"^{number}{letter}\s*·", lesson.title)]
            assert len(matches) == 1, "Workbook topic does not match the Course"
            by_code[f"{number}{letter}"] = matches[0].id
        matches = [lesson for lesson in lessons if re.match(rf"^{number}\s*·\s*Review and extension", lesson.title)]
        assert len(matches) == 1, "Missing native review Lesson"
        by_code[f"{number}R"] = matches[0].id
    desired = defaultdict(set)
    for number in range(1, 11):
        first = 4 + 6 * (number - 1)
        for offset, letter in enumerate("ABCD"):
            desired[by_code[f"{number}{letter}"]].add(first + offset)
        desired[by_code[f"{number}R"]].update((first+4, first+5))
    videos = (db.session.query(TeachingAssetPin.page_number, TeachingAsset.title)
        .join(TeachingAsset, TeachingAsset.id == TeachingAssetPin.asset_id)
        .filter(TeachingAssetPin.course_id == course_id, TeachingAsset.course_id == course_id,
                TeachingAssetPin.book_kind == "workbook", TeachingAssetPin.document_asset_id.is_(None)).all())
    video_pages = set()
    video_units = set()
    for page_number, title in videos:
        match = re.search(r"Workbook Video (\d{1,2})[abc]$", title)
        if match:
            number = int(match[1])
            assert 1 <= number <= 10 and 64 <= page_number <= 71
            desired[by_code[f"{number}C"]].add(page_number)
            video_pages.add(page_number)
            video_units.add(number)
    assert video_pages == set(range(64,72)) and video_units == set(range(1,11)), "Incomplete video map"
    master = {key: set(value) for key, value in desired.items()}
    group_ids = [row.group_id for row in GroupCurriculum.query.join(Group, Group.id == GroupCurriculum.group_id)
                 .filter(GroupCurriculum.course_id == course_id, Group.course_id == course_id, Group.status == "active")]
    if group_ids:
        source_material = aliased(Material)
        origins = defaultdict(set)
        rows = (db.session.query(Material.lesson_id, source_material.lesson_id)
            .join(GroupMaterialSource, GroupMaterialSource.material_id == Material.id)
            .join(source_material, source_material.id == GroupMaterialSource.course_material_id)
            .join(Lesson, Lesson.id == Material.lesson_id).join(Unit, Unit.id == Lesson.unit_id)
            .filter(Unit.group_id.in_(group_ids), source_material.lesson_id.in_(master)).all())
        for lesson_id, origin_id in rows:
            origins[lesson_id].add(origin_id)
        for lesson_id, origin_ids in origins.items():
            assert len(origin_ids) == 1, "Ambiguous Group-copy lineage"
            desired[lesson_id] = set(master[next(iter(origin_ids))])
        # Every active independent copy must have the full known mapping.
        for group_id in group_ids:
            copied_ids = {row.id for row in Lesson.query.join(Unit).filter(Unit.group_id == group_id)}
            assert len(copied_ids & desired.keys()) == 50, "Incomplete Group-copy lineage"
    expected = {(lesson_id, page_number, book.revision) for lesson_id, pages in desired.items() for page_number in pages}
    current = {(row.lesson_id, row.page_number, row.book_revision) for row in WorkbookLessonPage.query.filter_by(course_id=course_id)}
    assert not current or current == expected, "Existing Workbook map differs; refusing replacement"
    assert {page for _, page, _ in expected} == set(range(4,72)), "Unsafe page set"
    if apply and not current:
        db.session.add_all([WorkbookLessonPage(lesson_id=lesson_id, page_number=page_number,
            course_id=course_id, book_kind="workbook", book_revision=revision)
            for lesson_id, page_number, revision in sorted(expected)])
        db.session.commit()
    else:
        db.session.rollback()
    return {"status":"SUCCEEDED" if apply else "REVIEWED", "course_public_id":course_public_id,
        "mapped_lessons":len(desired), "mapping_rows":len(expected), "student_pages":68,
        "student_page_range":[4,71], "withheld_audio_scripts":[72,77], "private_answer_pages":[78,86],
        "independent_group_copies":len(group_ids), "already_present":bool(current),
        "publication_changed":False, "progress_changed":False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course-public-id", required=True)
    parser.add_argument("--book-sha256", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    assert re.fullmatch(r"[a-f0-9]{64}", args.book_sha256)
    app = create_app("production")
    with app.app_context():
        result = map_workbook(args.course_public_id, args.book_sha256, args.apply)
        Path(args.receipt).write_text(json.dumps(result,indent=2),encoding="utf-8")
        print(json.dumps(result))
