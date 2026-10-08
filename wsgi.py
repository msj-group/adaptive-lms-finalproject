"""Production WSGI factory. No development server, bootstrap or demo records."""
from app import create_app

application = create_app("production")
