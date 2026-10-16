"""Production WSGI rehearsal against disposable fictional data only."""
import http.client
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from database import database as db
from manage import backup_database, check_database, restore_database, seed_demo
from services.auth import register_user


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = db.DB_FILE
        db.DB_FILE = str(Path(self.tmp.name) / 'demo.db')
        seed_demo()
        password = secrets.token_urlsafe(24)
        register_user('deployment@example.com', password, password, 'Private Rehearsal Store')
        self.env = dict(os.environ, TCCP_ENV='production', TCCP_DB_PATH=db.DB_FILE,
                        TCCP_SECRET_KEY=secrets.token_hex(32), TCCP_READ_ONLY='1',
                        TCCP_ALLOW_REGISTRATION='0', TCCP_TRUSTED_HOSTS='localhost',
                        REHEARSAL_PASSWORD=password)

    def tearDown(self):
        db.DB_FILE = self.previous
        self.tmp.cleanup()

    def run_python(self, code, **overrides):
        return subprocess.run([sys.executable, '-B', '-c', code],
                              env=dict(self.env, **overrides), capture_output=True,
                              text=True, timeout=60)

    def test_demo_seed_is_isolated_and_refuses_repeat(self):
        with db.get_db_connection() as conn:
            store = conn.execute("SELECT id FROM stores WHERE kind='demo'").fetchone()[0]
            for table, count in [('products', 4), ('customers', 1), ('suppliers', 1), ('transactions', 6)]:
                self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table} WHERE store_id=?', (store,)).fetchone()[0], count)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM transactions WHERE customer_id IS NULL').fetchone()[0], 0)
        with self.assertRaises(ValueError):
            seed_demo(allow_existing=True)
        self.assertTrue(check_database()[0])

    def test_production_wsgi_pages_assets_auth_and_readonly(self):
        result = self.run_python('''
import os, re
from unittest.mock import patch
from web_app import app
client = app.test_client()
def get(path):
    return client.get(path, base_url='https://localhost')
assert get('/').status_code == 302
login = get('/login')
assert login.status_code == 200
assert 'Secure' in login.headers['Set-Cookie']
assert 'HttpOnly' in login.headers['Set-Cookie']
assert 'SameSite=Lax' in login.headers['Set-Cookie']
assert get('/register').status_code == 403
assert client.get('/login', base_url='https://untrusted.invalid').status_code == 400
assert client.post('/demo', base_url='https://localhost').status_code == 400
with client.session_transaction(base_url='https://localhost') as state:
    csrf = state['csrf_token']
response = client.post('/login', base_url='https://localhost', data=dict(
    csrf_token=csrf, email='deployment@example.com', password=os.environ['REHEARSAL_PASSWORD']))
assert response.status_code == 302
assert 'Private Rehearsal Store' in get('/').text
with client.session_transaction(base_url='https://localhost') as state:
    csrf = state['csrf_token']
assert client.post('/demo', base_url='https://localhost', data={'csrf_token':csrf}).status_code == 302
assets = set()
for path in ['/', '/sale', '/pos/designer', '/inventory', '/customers', '/suppliers',
             '/purchases', '/expenses', '/reports', '/financials', '/transactions', '/analytics']:
    response = get(path)
    assert response.status_code == 200, (path, response.status_code)
    assert 'Private Rehearsal Store' not in response.text, path
    assert 'no-store' in response.headers['Cache-Control']
    assert 'Strict-Transport-Security' in response.headers
    assets.update(re.findall(r'(?:src|href)="(/static/[^"?]+)', response.text))
assert assets
for path in assets:
    assert get(path).status_code == 200, path
for path in ['/sale/add', '/sale/checkout', '/customers/new', '/suppliers/new', '/pos/designer']:
    assert client.post(path, base_url='https://localhost', data={'csrf_token':csrf}).status_code == 403, path
from database.database import get_db_connection
with get_db_connection() as conn:
    tx = conn.execute("SELECT id FROM transactions WHERE store_id=(SELECT id FROM stores WHERE kind='demo') LIMIT 1").fetchone()[0]
assert get('/transactions/' + str(tx)).status_code == 200
assert get('/transactions/' + str(tx) + '/receipt').status_code == 200
import sqlite3
with patch('web_app.list_active_products', side_effect=sqlite3.OperationalError('private-storage-detail')):
    response = get('/')
    assert response.status_code == 503
    assert 'private-storage-detail' not in response.text
    assert 'Traceback' not in response.text
print('Production WSGI smoke passed; assets:', len(assets))
''')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_restart_migration_backup_restore_preserve_rows(self):
        query = '''import json; from database.database import get_db_connection
with get_db_connection() as conn:
 print(json.dumps({t:[list(r) for r in conn.execute('SELECT * FROM '+t+' ORDER BY id')] for t in ('stores','products','customers','suppliers','transactions','transaction_items')}))'''
        before = self.run_python(query)
        self.assertEqual(before.returncode, 0, before.stderr)
        backup = backup_database(Path(self.tmp.name) / 'backup.db')
        db.initialize_database(seed=False)
        db.initialize_database(seed=False)
        after = self.run_python(query)
        self.assertEqual(json.loads(before.stdout), json.loads(after.stdout))
        restored = restore_database(backup, Path(self.tmp.name) / 'restored.db')
        self.assertTrue(check_database(restored)[0])
        copy = self.run_python(query, TCCP_DB_PATH=str(restored))
        self.assertEqual(json.loads(before.stdout), json.loads(copy.stdout))
        result = subprocess.run([sys.executable, '-m', 'gunicorn', '--check-config', 'web_app:app'],
                                env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_storage_fails_without_creating_database(self):
        missing = str(Path(self.tmp.name) / 'missing.db')
        result = self.run_python('import web_app', TCCP_DB_PATH=missing)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(Path(missing).exists())

    @unittest.skipUnless(hasattr(socket, 'AF_UNIX'), 'Gunicorn rehearsal requires Unix')
    def test_gunicorn_boots_and_serves_login(self):
        endpoint = str(Path(self.tmp.name) / 'wsgi.sock')
        with tempfile.TemporaryFile(mode='w+') as log:
            process = subprocess.Popen(
                [sys.executable, '-m', 'gunicorn', '--bind', 'unix:' + endpoint,
                 '--workers', '1', '--threads', '2', '--timeout', '60', 'web_app:app'],
                env=self.env, stdout=log, stderr=log,
            )
            try:
                deadline = time.monotonic() + 20
                while not Path(endpoint).exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.05)
                if not Path(endpoint).exists():
                    log.seek(0)
                    self.fail('Gunicorn did not create its listener: ' + log.read())
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as transport:
                    transport.settimeout(20)
                    transport.connect(endpoint)
                    transport.sendall(b'GET /login HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                    response = http.client.HTTPResponse(transport)
                    response.begin()
                    self.assertEqual(response.status, 200)
                    self.assertNotIn(b'Traceback', response.read())
            finally:
                process.terminate()
                process.wait(timeout=20)
            self.assertTrue(check_database()[0])
