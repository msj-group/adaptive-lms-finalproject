"""Page map for an already authorized Group; no writes or implicit publication."""
from collections import defaultdict

from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import CourseReferenceBook, WorkbookLessonPage, Lesson, Unit
from app.services.student_lessons import outline_units


def workbook_access(student_id, group):
    book = (CourseReferenceBook.query.options(joinedload(CourseReferenceBook.uploaded_file))
            .filter_by(course_id=group.course_id, kind="workbook").first())
    if book is None or book.uploaded_file.extension != "pdf":
        return None
    units = outline_units(group.id, student_id, book="workbook")
    visible = {lesson["public_id"] for unit in units for lesson in unit["lessons"]}
    rows = (db.session.query(WorkbookLessonPage.page_number, Lesson.public_id)
            .join(Lesson, Lesson.id == WorkbookLessonPage.lesson_id)
            .join(Unit, Unit.id == Lesson.unit_id)
            .filter(WorkbookLessonPage.course_id == group.course_id,
                    WorkbookLessonPage.book_kind == "workbook",
                    WorkbookLessonPage.book_revision == book.revision,
                    WorkbookLessonPage.page_number <= book.page_count,
                    Unit.in_group(group.id, group.course_id)).all())
    owners = defaultdict(set)
    for number, lesson in rows:
        owners[number].add(lesson)
    # A physical page can reveal several Lessons. All owners must be visible.
    allowed = {number for number, lessons in owners.items() if lessons <= visible}
    pages = defaultdict(list)
    for number, lesson in rows:
        if number in allowed:
            pages[lesson].append(number)
    pages = {lesson: sorted(set(numbers)) for lesson, numbers in pages.items()}
    outline = [dict(unit, lessons=[lesson for lesson in unit["lessons"]
                                  if pages.get(lesson["public_id"])]) for unit in units]
    return book, [unit for unit in outline if unit["lessons"]], pages, allowed
