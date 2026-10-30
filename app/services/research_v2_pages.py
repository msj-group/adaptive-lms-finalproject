"""Closed semantic page/context mapping; never store raw request URLs."""
from app.services.research_event_dictionary import page_id_for_endpoint


def page_code(endpoint):
    if endpoint == "workspace.search":
        return "search"
    if endpoint == "workspace.help_page":
        return "help"
    page = page_id_for_endpoint(endpoint)
    if page is None:
        return None
    if page == "workspace.student_courses":
        return "course_list"
    if page == "workspace.student_course" or page == "student.group_units":
        return "course"
    if page == "account.settings":
        return "settings"
    if page.startswith("messages."):
        return "messaging"
    if page.startswith("notifications."):
        return "notifications"
    name = page.split(".", 1)[1]
    for prefix, code in (("assignment", "assignment"), ("quiz", "quiz"), ("listening", "listening"),
                         ("speaking", "speaking"), ("lesson", "lesson"), ("attendance", "attendance"),
                         ("grade", "grades"), ("group_grade", "grades"), ("announcement", "announcement"),
                         ("discussion", "discussion"), ("episode_record", "records")):
        if name.startswith(prefix):
            return code
    return {"dashboard": "dashboard", "activities": "activity_list", "records": "records",
            "calendar": "calendar", "search": "search"}.get(name, "unknown")
