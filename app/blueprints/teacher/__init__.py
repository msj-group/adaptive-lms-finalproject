from flask import Blueprint

teacher_bp = Blueprint("teacher", __name__, url_prefix="/teacher")

from app.blueprints.teacher import routes  # noqa: E402,F401
from app.blueprints.teacher import units  # noqa: E402,F401
from app.blueprints.teacher import lessons  # noqa: E402,F401
from app.blueprints.teacher import materials  # noqa: E402,F401
from app.blueprints.teacher import assignments  # noqa: E402,F401
from app.blueprints.teacher import feedback  # noqa: E402,F401
from app.blueprints.teacher import quizzes  # noqa: E402,F401
from app.blueprints.teacher import listening  # noqa: E402,F401
