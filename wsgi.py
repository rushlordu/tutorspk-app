"""Production entrypoint for Gunicorn/hosting platforms."""
from app import app

application = app
