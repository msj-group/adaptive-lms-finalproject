"""Read-only presentation/filtering over already authorized Teacher cards.

The dashboard query owns assignment scope and student eligibility. This module
never queries the database, changes authorization, or treats term dates as an
assignment lifecycle. All choices come from the same authorized card payload.
"""


class InvalidGroupFilter(ValueError):
    def __init__(self, status_code):
        super().__init__("Invalid group filter")
        self.status_code = status_code


def teacher_group_directory(cards, today, args):
    query = " ".join(args.get("q", "").split())
    if len(query) > 120:
        raise InvalidGroupFilter(400)
    filters = {name: args.get(name, "all") or "all"
               for name in ("course", "term", "context", "period")}
    if any(len(filters[name]) > 36 for name in ("course", "term")):
        raise InvalidGroupFilter(404)
    if filters["context"] not in {"all", "operational", "archived"}:
        raise InvalidGroupFilter(400)
    if filters["period"] not in {"all", "current", "upcoming", "ended"}:
        raise InvalidGroupFilter(400)
    filters["q"] = query

    courses, terms = {}, {}
    for card in cards:
        courses[card["course_public_id"]] = {
            "public_id": card["course_public_id"],
            "title": card["course_title"], "level_name": card["level_name"],
        }
        terms[card["term_public_id"]] = {
            "public_id": card["term_public_id"],
            "name": card["term_name"], "start": card["term_start"],
        }
    for name, options in (("course", courses), ("term", terms)):
        if filters[name] != "all" and filters[name] not in options:
            raise InvalidGroupFilter(404)

    visible = []
    needle = query.casefold()
    for card in cards:
        if filters["course"] != "all" and card["course_public_id"] != filters["course"]:
            continue
        if filters["term"] != "all" and card["term_public_id"] != filters["term"]:
            continue
        context = "operational" if card["operational"] else "archived"
        if filters["context"] != "all" and context != filters["context"]:
            continue
        period = ("upcoming" if card["term_start"] > today
                  else "ended" if card["term_end"] < today else "current")
        if filters["period"] != "all" and period != filters["period"]:
            continue
        haystack = " ".join(card[name] for name in (
            "group_name", "course_title", "level_name", "term_name"))
        if needle and needle not in haystack.casefold():
            continue
        visible.append(dict(card, term_period=period))

    return {
        "cards": visible, "assignment_count": len(cards), "filters": filters,
        "filters_active": bool(query) or any(filters[name] != "all"
            for name in ("course", "term", "context", "period")),
        "courses": sorted(courses.values(), key=lambda item: (
            item["title"].casefold(), item["level_name"].casefold(), item["public_id"])),
        "terms": sorted(terms.values(), key=lambda item: (
            item["start"], item["name"].casefold(), item["public_id"]), reverse=True),
    }
