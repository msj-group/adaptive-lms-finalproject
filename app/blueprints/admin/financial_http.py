"""Private response headers shared by current and compatibility financial views."""
from functools import wraps
from flask import make_response

def _financial_response(view):
    """Send ``Cache-Control: private, no-store`` and ``Vary: Cookie`` on every
    response this view returns -- rendered pages and redirects alike."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        response = make_response(view(*args, **kwargs))
        response.headers['Cache-Control'] = 'private, no-store'
        response.vary.add('Cookie')
        return response
    return wrapped
