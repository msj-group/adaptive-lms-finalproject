"""Private, read-only snapshots; every poll repeats Researcher authorization."""
from flask import jsonify, request, current_app, abort
from app.blueprints.research import research_bp
from app.models import now_ms
from app.security.decorators import roles_required
from app.services.research_live import snapshot


@research_bp.get('/live')
@roles_required('researcher')
def live():
    return jsonify(snapshot(now_ms()))


@research_bp.get('/activity-chart')
@roles_required('researcher')
def activity_chart():
    from datetime import timedelta
    from sqlalchemy import func
    from app.extensions import db
    from app.models import ResearchSession
    from app.models.research_observations import ResearchObservation as Event
    from app.services.research_analytics import dashboard_filters
    from app.services.research_observation_queries import session_conditions
    from app.services.research_charts import activity_chart as coordinates
    try:
        filters = dashboard_filters(request.args, now_ms())
    except (ValueError, OverflowError):
        abort(400)
    day = func.floor(Event.occurred_at_ms / 86400000)
    counts = dict(db.session.query(day, func.count()).join(ResearchSession, ResearchSession.id == Event.session_id).filter(
        *session_conditions(current_app.config['RESEARCH_DATA_PROVENANCE'], {'version_number': filters['version_number']}),
        Event.occurred_at_ms >= filters['lower'], Event.occurred_at_ms < filters['upper']).group_by(day).all())
    date, daily = filters['start'], []
    while date <= filters['end']:
        bucket = (date - type(date)(1970, 1, 1)).days
        daily.append(dict(label=date.isoformat(), count=counts.get(bucket, 0)))
        date += timedelta(days=1)
    return jsonify(coordinates(daily))
