"""
Generic WSGI entry point (gunicorn / uWSGI / mod_wsgi):

    gunicorn wsgi:application
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app import app as application  # noqa: E402
