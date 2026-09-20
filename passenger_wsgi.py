"""
Passenger entry point (cPanel "Setup Python App" / CloudLinux).

Startup file : passenger_wsgi.py
Entry point  : application
"""

import os
import sys
import traceback

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Passenger keeps the process' working directory unpredictable on some hosts.
os.chdir(BASE_DIR)

try:
    from wsgi import application  # noqa: E402
except Exception:
    # Leave a readable trace next to the app, then let Passenger show its error.
    with open(os.path.join(BASE_DIR, "passenger_error.log"), "a") as f:
        traceback.print_exc(file=f)
    raise
