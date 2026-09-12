from flask import Blueprint

student_bp = Blueprint("student", __name__, url_prefix="/student")

from app.blueprints.student import routes  # noqa: E402,F401
from app.blueprints.student import learning  # noqa: E402,F401
from app.blueprints.student import materials  # noqa: E402,F401
from app.blueprints.student import search  # noqa: E402,F401
from app.blueprints.student import assignments  # noqa: E402,F401
from app.blueprints.student import quizzes  # noqa: E402,F401
from app.blueprints.student import listening  # noqa: E402,F401
from app.blueprints.student import speaking  # noqa: E402,F401
from app.blueprints.student import attendance  # noqa: E402,F401
from app.blueprints.student import grades  # noqa: E402,F401
