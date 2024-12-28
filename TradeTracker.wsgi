#!/usr/bin/python
import sys
import os

sys.path.insert(0, '/var/www/TradeTracker')

os.environ['FLASK_ENV'] = 'production'

try:
    from main import app as application
except Exception as e:
    with open('/var/www/TradeTracker/wsgi_error.log', 'w') as f:
        f.write(str(e) + "\n")
    raise
