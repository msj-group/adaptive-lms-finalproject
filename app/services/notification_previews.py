"""Compact device previews from immutable, server-authored notification records.

Only owned boilerplate is translated. Names, authored titles and message subjects
are preserved; private message bodies are never queried for a preview.
"""
import re
from app.i18n import gettext_in_language

_PATTERNS = (
    (r"You are now enrolled in the group '(.*)'\. .*", (1,)),
    (r"Your enrollment in the group '(.*)' has been withdrawn\. .*", (1,)),
    (r"You are now assigned to teach the group '(.*)'\. .*", (1,)),
    (r"You are no longer assigned to teach the group '(.*)'\.", (1,)),
    (r"(?:A class time was (?:added|changed|removed|restored)|The class schedule was changed) for the group '(.*)'\. .*", (1,)),
    (r"The lesson '(.*)' is now available in the group '(.*)'\.", (2, 1)),
    (r"The material '(.*)' was added to the lesson '(.*)' in the group '(.*)'\.", (3, 2, 1)),
    (r"A new center announcement was published: '(.*)'\.", (1,)),
    (r"A new announcement was published for the (?:course|group) '(.*)': '(.*)'\.", (1, 2)),
    (r"(.*) sent you a message in the conversation '(.*)'\.", (1, 2)),
    (r"A new discussion topic was started in the group '(.*)': '(.*)'\.", (1, 2)),
    (r"New (?:workbook|course) content is available for (.*)\.", (1,)),
    (r"The (?:teacher|administrator) updated the curriculum for (.*)\.", (1,)),
    (r"Course content for (.*) has changed\.", (1,)),
    (r"The teacher changed which (?:workbook|course) content is visible in (.*)\.", (1,)),
)


def preview(title, message, language):
    language = language if language in ('ar', 'en') else 'en'
    body = None
    for pattern, fields in _PATTERNS:
        match = re.fullmatch(pattern, message, re.S)
        if match:
            body = ' · '.join(match.group(index) for index in fields)
            break
    if body is None:
        body = gettext_in_language(message, language)
    def compact(value, limit):
        value = ' '.join(value.split())
        return value if len(value) <= limit else value[:limit - 1].rstrip() + '…'
    return dict(title=compact(gettext_in_language(title, language), 80),
                body=compact(body, 160), lang=language, dir='rtl' if language == 'ar' else 'ltr')
