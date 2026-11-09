"""Refresh explicit, authorized communication regions without rendering a workspace."""
import re

from flask import make_response, render_template, request
from flask_login import current_user
from werkzeug.http import generate_etag


LIVE_HEADER = 'X-AELMS-Communication'
TAIL_HEADER = 'X-AELMS-Communication-Tail'
_VOLATILE_FIELDS = re.compile(r'(<input\b[^>]*\bname="(?:csrf_token|message_state)"[^>]*\bvalue=")[^"]*"')
FRAGMENTS = {
    'messages/inbox.html': 'messages/_live_inbox.html',
    'messages/thread.html': 'messages/_live_thread.html',
    'student/discussions/list.html': 'student/discussions/_live.html',
    'student/discussions/detail.html': 'student/discussions/_live.html',
    'teacher/discussions/list.html': 'teacher/discussions/_live.html',
    'teacher/discussions/detail.html': 'teacher/discussions/_live.html',
    'student/announcements/list.html': 'student/announcements/_live_list.html',
    'student/announcements/detail.html': 'student/announcements/_live_detail.html',
    'teacher/announcements/feed.html': 'teacher/announcements/_feed_results.html',
    'teacher/announcements/group_list.html': 'teacher/announcements/_live_group.html',
    'teacher/announcements/group_detail.html': '_announcement_live_body.html',
    'teacher/announcements/detail.html': '_announcement_live_body.html',
    'admin/announcements/overview.html': 'admin/announcements/_query_results.html',
    'admin/announcements/detail.html': '_announcement_live_body.html',
    'notifications/inbox.html': 'notifications/_inbox_results.html',
    'notifications/_preview.html': 'notifications/_preview.html',
    'student/assignments/detail.html': 'student/assignments/_live_feedback.html',
    'student/speaking/receipt.html': 'student/speaking/_live_feedback.html',
    'student/grades/detail.html': 'student/grades/_live_results.html',
    'teacher/submissions/feedback.html': 'teacher/review/_live_feedback.html',
    'teacher/speaking/feedback.html': 'teacher/review/_live_feedback.html',
    'teacher/dashboard.html': 'teacher/_live_dashboard_announcements.html',
}


def live_requested():
    return request.method == 'GET' and request.headers.get(LIVE_HEADER) == '1'


def following_replies():
    return live_requested() and request.headers.get(TAIL_HEADER) == '1'


def render_communication_fragment(template, context):
    fragment = FRAGMENTS.get(template) if live_requested() else None
    if fragment is None:
        return None
    response = make_response(render_template(fragment, **context))
    response.headers[LIVE_HEADER] = '1'
    response.headers['X-AELMS-Viewer'] = f'{current_user.public_id}:{current_user.auth_version}'
    response.headers['Cache-Control'] = 'private, no-store'
    response.vary.add('Cookie')
    response.vary.add(LIVE_HEADER)
    response.vary.add(TAIL_HEADER)
    # Fresh signed form tokens are emitted when content changes. A token's
    # timestamp alone must not cause an unchanged timeline to repaint.
    stable = _VOLATILE_FIELDS.sub(r'\1"', response.get_data(as_text=True))
    response.set_etag(generate_etag(stable.encode('utf-8')))
    return response.make_conditional(request)
