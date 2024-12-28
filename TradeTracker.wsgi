#!/usr/bin/python
import sys
import os

sys.path.insert(0, '/var/www/TradeTracker')
os.environ['FLASK_ENV'] = 'production'

with open('/var/www/TradeTracker/wsgi_debug.log', 'w') as f:
    f.write('WSGI loaded successfully\n')

from main import app as application
