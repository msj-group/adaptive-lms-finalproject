"""Serve private page notes only after the caller authorizes the PDF page."""
import json
import re
from uuid import UUID

from flask import abort, jsonify, request
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import BookAnnotation
from app.services.actor_authorization import require_current_actor

MAX_BYTES = 384 * 1024
MAX_MARKS = 160
MAX_POINTS = 20000
COLORS = frozenset({"#255ec5", "#192f4a", "#de4545", "#e8bd32", "#35a77c"})


def _identifier(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError):
        abort(400)
    return value


def _number(value, upper=10000):
    if type(value) is not int or not 0 <= value <= upper:
        abort(400)
    return value


def validate_mark(mark):
    if not isinstance(mark, dict):
        abort(400)
    _identifier(mark.get("id"))
    kind = mark.get("kind")
    expected = {"id", "kind", "color", "size"}
    if kind == "text":
        expected.update(("x", "y", "text"))
        if not isinstance(mark.get("text"), str) or not 1 <= len(mark["text"].strip()) <= 1200 or "\x00" in mark["text"]:
            abort(400)
        _number(mark.get("x"))
        _number(mark.get("y"))
    elif kind in {"pen", "highlight"}:
        expected.add("points")
        points = mark.get("points")
        if not isinstance(points, list) or not 1 <= len(points) <= 1200:
            abort(400)
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                abort(400)
            for value in point:
                _number(value)
    else:
        abort(400)
    if set(mark) != expected or mark.get("color") not in COLORS:
        abort(400)
    if _number(mark.get("size"), 600) == 0:
        abort(400)
    return mark


def merge_marks(existing, additions, removals):
    if (not isinstance(additions, list) or len(additions) > MAX_MARKS
            or not isinstance(removals, list) or len(removals) > MAX_MARKS):
        abort(400)
    remove_ids = {_identifier(value) for value in removals}
    result = {mark["id"]: mark for mark in existing if mark["id"] not in remove_ids}
    for incoming in additions:
        mark = validate_mark(incoming)
        previous = result.get(mark["id"])
        if previous is not None and previous != mark:
            abort(409)
        result[mark["id"]] = mark
    marks = list(result.values())
    if (len(marks) > MAX_MARKS or sum(len(mark.get("points", ())) for mark in marks) > MAX_POINTS
            or len(json.dumps(marks, ensure_ascii=False).encode("utf-8")) > MAX_BYTES):
        abort(413)
    return marks


def serve_book_annotations(document, page_number):
    if not current_user.is_authenticated:
        abort(401)
    if request.headers.get("X-Book-Notes-Owner") != current_user.public_id:
        abort(403)
    sha = document.uploaded_file.sha256
    if not re.fullmatch(r"[a-f0-9]{64}", sha or ""):
        abort(404)
    owner = current_user.id
    identity = dict(user_id=owner, book_sha256=sha, page_number=page_number)
    if request.method == "POST":
        if not request.is_json:
            abort(415)
        if request.content_length is not None and request.content_length > MAX_BYTES:
            abort(413)
        body = request.stream.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            abort(413)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            abort(400)
        if not isinstance(payload, dict) or set(payload) != {"add", "remove"}:
            abort(400)
        # One account lock serializes page creation and idempotent device retries.
        db.session.rollback()
        require_current_actor(owner, current_user.role)
        record = BookAnnotation.query.filter_by(**identity).with_for_update().first()
        before = record.marks if record else []
        marks = merge_marks(before, payload["add"], payload["remove"])
        if marks != before:
            if record is None:
                record = BookAnnotation(**identity, marks=marks)
                db.session.add(record)
            else:
                record.marks = marks
                record.revision += 1
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            abort(409)
    else:
        record = BookAnnotation.query.filter_by(**identity).first()
    response = jsonify(book=sha, marks=record.marks if record else [], revision=record.revision if record else 0)
    response.headers.update({"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
    response.vary.add("Cookie")
    return response
