FROM python:3.14.6-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FLASK_ENV=production
WORKDIR /opt/youth-centre
COPY requirements-production.txt ./
RUN pip install --no-cache-dir -r requirements-production.txt && useradd --create-home --uid 10001 centre
COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts
COPY deploy ./deploy
COPY wsgi.py ./
RUN mkdir -p /srv/youth-centre/private-files /opt/youth-centre/instance && chown -R centre:centre /srv/youth-centre /opt/youth-centre/instance
USER centre
ENV WSGI_BIND=0.0.0.0:8000 MATERIAL_STORAGE_ROOT=/srv/youth-centre/private-files
EXPOSE 8000
# Deliberately no automatic migration, seeding, account creation or collection.
CMD ["gunicorn", "-c", "deploy/gunicorn.conf.py", "wsgi:application"]
