"""Isolated Playwright checks: fake test accounts and in-memory emails only.

Install Playwright and Chromium, then run this script. A separate test server
and test database are created automatically. No production credentials needed.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
os.environ['RUN_BROWSER_TESTS'] = '1'
import django
django.setup()
from django.core.management import call_command

call_command('test', 'accounts.test_browser')
