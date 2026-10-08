"""Bounded read-only dashboard projections. No LMS identities or collection writes.

Range filters use UTC dates; at most 90 daily buckets and closed-set grouped
counts are returned. A browser cannot change the deployment's provenance.
Null ratings remain absent; no emotion, prediction or causal inference is made.
"""
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import and_, case, func

from app.extensions import db
from app.models import ResearchConfiguration, ResearchEvent, ResearchFeedbackPrompt, ResearchSession
from app.services.research_event_dictionary import PAGE_AREAS, REPORTING_AREAS
from app.services.research_scope import export_session_provenances


def dashboard_filters(args, as_of_ms):
    today = datetime.fromtimestamp(as_of_ms / 1000, timezone.utc).date()
    end = date.fromisoformat(args.get("to") or today.isoformat())
    start = date.fromisoformat(args.get("from") or (end - timedelta(days=14)).isoformat())
    if not 0 <= (end - start).days <= 89 or end > today:
        raise ValueError("Use an ordered range of at most 90 days, through today.")
    raw_version = (args.get("version") or "").strip()
    version = int(raw_version) if raw_version else None
    if version is not None and not 1 <= version <= 1_000_000:
        raise ValueError("Invalid configuration version.")
    epoch = lambda d: int(datetime.combine(d, time.min, timezone.utc).timestamp() * 1000)
    return {"from": start.isoformat(), "to": end.isoformat(), "version": raw_version,
            "start": start, "end": end, "lower": epoch(start),
            "upper": min(epoch(end + timedelta(days=1)), as_of_ms + 1), "version_number": version}


def _scope(provenance, filters, moment_column):
    terms = [ResearchSession.provenance.in_(export_session_provenances(provenance)),
             moment_column >= filters["lower"], moment_column < filters["upper"]]
    if filters["version_number"] is not None:
        terms.append(ResearchConfiguration.version_number == filters["version_number"])
    return terms


def _events(*columns):
    return (db.session.query(*columns)
            .select_from(ResearchEvent)
            .join(ResearchSession, ResearchSession.id == ResearchEvent.session_id)
            .join(ResearchConfiguration, ResearchConfiguration.id == ResearchSession.configuration_id))


def _prompts(*columns):
    return (db.session.query(*columns)
            .select_from(ResearchFeedbackPrompt)
            .join(ResearchSession, ResearchSession.id == ResearchFeedbackPrompt.session_id)
            .join(ResearchConfiguration, ResearchConfiguration.id == ResearchFeedbackPrompt.configuration_id))


def prompt_state_expression(as_of_ms):
    """SQL counterpart of derived_prompt_state, with identical strict expiries."""
    p, c = ResearchFeedbackPrompt, ResearchConfiguration
    responded = and_(p.responded_at_ms.isnot(None), p.responded_at_ms <= as_of_ms)
    displayed = and_(p.displayed_at_ms.isnot(None), p.displayed_at_ms <= as_of_ms)
    return case(
        (and_(p.status == "answered", responded), "answered"),
        (and_(p.status == "dismissed", responded), "dismissed"),
        (and_(displayed, as_of_ms - p.displayed_at_ms > c.response_window_seconds * 1000), "no_response"),
        (displayed, "awaiting_response"),
        (as_of_ms - p.offered_at_ms > c.offer_ttl_seconds * 1000, "offer_expired"),
        else_="awaiting_display")


def _series(rows):
    """Count bars share a linear 0..largest-count axis, never a hidden rate."""
    maximum = max((int(value) for _label, value in rows), default=0)
    return {"rows": [{"label": label, "count": int(value),
                      "width": round(int(value) / maximum * 100, 3) if maximum else 0}
                     for label, value in rows], "maximum": maximum,
            "total": sum(int(value) for _label, value in rows)}


def dashboard_analytics(provenance, filters, as_of_ms):
    e, p, s = ResearchEvent, ResearchFeedbackPrompt, ResearchSession
    event_scope = _scope(provenance, filters, e.occurred_at_ms)
    prompt_scope = _scope(provenance, filters, p.offered_at_ms)
    day = func.floor(e.occurred_at_ms / 86_400_000)
    daily_counts = _events(day, func.count(e.id)).filter(*event_scope).group_by(day).all()
    daily_map = {int(bucket): int(count) for bucket, count in daily_counts}
    daily = []
    current = filters["start"]
    while current <= filters["end"]:
        bucket = int(datetime.combine(current, time.min, timezone.utc).timestamp()) // 86400
        daily.append((current.isoformat(), daily_map.get(bucket, 0)))
        current += timedelta(days=1)
    event_types = _events(e.event_type, func.count(e.id)).filter(*event_scope).group_by(e.event_type).all()
    sources = _events(e.source, func.count(e.id)).filter(*event_scope).group_by(e.source).all()
    composition = _events(s.provenance, func.count(e.id)).filter(*event_scope).group_by(s.provenance).all()
    pages = _events(e.page_id, func.count(e.id)).filter(*event_scope, e.event_type == "page_view").group_by(e.page_id).all()
    area_counts = {area: 0 for area in ("core",) + REPORTING_AREAS + ("unknown",)}
    for page_id, count in pages:
        area_counts[PAGE_AREAS.get(page_id, "unknown")] += int(count)
    state = prompt_state_expression(as_of_ms)
    states = dict(_prompts(state, func.count(p.id)).filter(*prompt_scope).group_by(state).all())
    ratings = dict(_prompts(p.rating, func.count(p.id)).filter(
        *prompt_scope, p.status == "answered", p.responded_at_ms <= as_of_ms,
        p.rating.isnot(None)).group_by(p.rating).all())
    shown = sum(states.get(key, 0) for key in ("answered", "dismissed", "no_response", "awaiting_response"))
    answered = int(states.get("answered", 0))
    counter_names = ("events_accepted", "events_duplicate", "events_invalid", "events_late", "events_dropped_client")
    delivery = (db.session.query(func.count(s.id), *(func.coalesce(func.sum(getattr(s, key)), 0) for key in counter_names))
                .join(ResearchConfiguration, ResearchConfiguration.id == s.configuration_id)
                .filter(*_scope(provenance, filters, s.started_at_ms)).one())
    return {"daily": _series(daily), "types": _series(sorted(event_types, key=lambda r: (-r[1], r[0]))),
            "sources": _series(sources), "composition": _series(composition),
            "coverage": _series(list(area_counts.items())),
            "ratings": _series([(str(value), ratings.get(value, 0)) for value in range(1, 6)]),
            "states": states, "shown": shown, "answered": answered, "missing": shown - answered,
            "label_rate": round(answered / shown * 100, 1) if shown else None,
            "delivery_sessions": int(delivery[0]),
            "delivery": _series([(key.replace("events_", "").replace("_", " "), int(count))
                                 for key, count in zip(counter_names, delivery[1:])])}
