from functools import wraps

from flask import abort, g
from flask_login import current_user, login_required


def roles_required(*roles):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            if not hasattr(g, "authenticated_actor"):
                g.authenticated_actor = (current_user.id, current_user.auth_version, current_user.role)
            return view_func(*args, **kwargs)

        return wrapped

    return decorator
