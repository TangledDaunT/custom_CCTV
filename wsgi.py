"""Production WSGI entry point.

Gunicorn is intentionally configured with one worker because one physical USB
camera can only be owned by one capture process.
"""
from app import app, start_services

start_services()
