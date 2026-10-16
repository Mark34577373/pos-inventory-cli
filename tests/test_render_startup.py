"""Disposable Render bootstrap and production import regression checks."""
import http.client
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path


class RenderStartupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tccp-render-test-', dir='/tmp')
        self.path = Path(self.tmp.name) / 'demo.db'
        self.env = dict(os.environ, TCCP_ENV='production', TCCP_READ_ONLY='1',
                        TCCP_ALLOW_REGISTRATION='0', TCCP_EPHEMERAL_DEMO='1',
                        TCCP_SECRET_KEY=secrets.token_hex(32), TCCP_DB_PATH=str(self.path),
                        TCCP_TRUSTED_HOSTS='localhost')

    def tearDown(self):
        self.tmp.cleanup()

    def run_command(self, *args, **changes):
        return subprocess.run([sys.executable, '-B', *args], env=dict(self.env, **changes),
                              capture_output=True, text=True, timeout=30)

    def prepare(self):
        result = self.run_command('manage.py', 'prepare-render-demo')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def dump(self):
        with sqlite3.connect(self.path) as conn:
            return '\n'.join(conn.iterdump())

    def test_fresh_boot_and_restart_preserve_exact_records(self):
        missing = self.run_command('-c', 'import web_app')
        self.assertNotEqual(missing.returncode, 0)
        self.assertFalse(self.path.exists())
        self.prepare()
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM products').fetchone()[0], 4)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0], 6)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM users').fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM stores WHERE kind='demo'").fetchone()[0], 1)
        before = self.dump()
        self.prepare()
        self.assertEqual(self.dump(), before)
        self.assertFalse(list(self.path.parent.glob('*.backup')))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        result = self.run_command('-c', '''
from web_app import app
assert not app.debug
assert app.config['DEMO_READ_ONLY']
assert app.config['SESSION_COOKIE_SECURE']
client = app.test_client()
def get(path): return client.get(path, base_url='https://localhost')
assert get('/').status_code == 302
assert get('/register').status_code == 403
assert client.get('/login', base_url='https://evil.invalid').status_code == 400
assert client.post('/demo', base_url='https://localhost').status_code == 400
get('/login')
with client.session_transaction(base_url='https://localhost') as state:
 token = state['csrf_token']
assert client.post('/register', base_url='https://localhost', data={'csrf_token':token}).status_code == 403
assert client.post('/demo', base_url='https://localhost', data={'csrf_token':token}).status_code == 302
for path in ['/', '/inventory', '/sale', '/transactions', '/reports']:
 assert get(path).status_code == 200, path
assert client.post('/sale/add', base_url='https://localhost', data={'csrf_token':token, 'product_id':1}).status_code == 403
from pathlib import Path
import os
database_before = Path(os.environ['TCCP_DB_PATH']).read_bytes()
simulation = app.test_client()
assert simulation.get('/demo/pos', base_url='https://localhost').status_code == 200
with simulation.session_transaction(base_url='https://localhost') as state:
 demo_csrf, demo_token = state['csrf_token'], state['interactive_demo_token']
assert simulation.post('/demo/cart', base_url='https://localhost', data=dict(csrf_token=demo_csrf,product_id='notebook',quantity='1',action='add')).status_code == 303
from demo_routes import memory
nonce = memory.states[demo_token]['nonce']
assert simulation.post('/demo/checkout', base_url='https://localhost', data=dict(csrf_token=demo_csrf,checkout_token=nonce,payment_method='Card')).status_code == 303
assert simulation.get('/demo/receipt', base_url='https://localhost').status_code == 200
assert Path(os.environ['TCCP_DB_PATH']).read_bytes() == database_before
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.dump(), before)

    def test_empty_initialized_database_is_seeded_once(self):
        result = self.run_command('manage.py', 'init')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.prepare()
        before = self.dump()
        self.prepare()
        self.assertEqual(before, self.dump())

    def test_private_records_are_refused_without_modification(self):
        result = self.run_command('-c', 'from database.database import initialize_database; initialize_database()')
        self.assertEqual(result.returncode, 0, result.stderr)
        before = self.path.read_bytes()
        result = self.run_command('manage.py', 'prepare-render-demo')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Refusing non-demo', result.stdout)
        self.assertEqual(before, self.path.read_bytes())

    def test_invalid_profile_and_paths_fail_before_creation(self):
        for key, value in [('TCCP_EPHEMERAL_DEMO', '0'), ('TCCP_READ_ONLY', '0'),
                           ('TCCP_ALLOW_REGISTRATION', '1'), ('TCCP_SECRET_KEY', ''),
                           ('TCCP_DB_PATH', '/var/data/demo.db')]:
            with self.subTest(key=key):
                result = self.run_command('manage.py', 'prepare-render-demo', **{key:value})
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.path.exists())

    def test_corrupt_existing_file_is_not_replaced(self):
        self.path.write_bytes(b'Not a SQLite database')
        result = self.run_command('manage.py', 'prepare-render-demo')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.path.read_bytes(), b'Not a SQLite database')

    def test_existing_empty_file_is_backed_up_before_initialization(self):
        self.path.touch()
        self.prepare()
        self.assertEqual(len(list(self.path.parent.glob('*.backup'))), 1)
        with sqlite3.connect(self.path) as conn:
            self.assertIsNotNone(conn.execute('SELECT 1 FROM schema_migrations WHERE version=27').fetchone())

    def test_day14_demo_database_upgrades_without_resetting_sales(self):
        # Build a genuine supported older schema rather than changing version markers.
        result = self.run_command('-c', '''
from contextlib import closing
from database import database as db
from database.migrations import migrate_identity
with closing(db.get_db_connection()) as conn:
 with conn:
  conn.execute('BEGIN IMMEDIATE')
  db._initialize_schema(conn, False)
  migrate_identity(conn)
  store = conn.execute("INSERT INTO stores(name,kind) VALUES ('Fictional older demo','demo')").lastrowid
  conn.execute("INSERT INTO products(name,price,price_cents,quantity,store_id) VALUES ('Fictional preserved product',1.25,125,9,?)", (store,))
  tx = conn.execute("INSERT INTO transactions(timestamp,total,total_cents,payment_method,store_id) VALUES ('2026-10-15 12:00:00',1.25,125,'Card',?)", (store,)).lastrowid
  conn.execute("INSERT INTO transaction_items(transaction_id,product_id,quantity,price,price_cents,product_name,store_id) VALUES (?,1,1,1.25,125,'Fictional preserved product',?)", (tx,store))
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        with sqlite3.connect(self.path) as conn:
            before = conn.execute('SELECT id,total_cents,store_id FROM transactions').fetchall()
        self.prepare()
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute('SELECT id,total_cents,store_id FROM transactions').fetchall(), before)
            self.assertEqual(conn.execute('SELECT quantity,price_cents FROM products').fetchall(), [(9,125)])
            self.assertIsNotNone(conn.execute('SELECT 1 FROM schema_migrations WHERE version=27').fetchone())
        self.assertEqual(len(list(self.path.parent.glob('*.backup'))), 1)
        after = self.dump()
        self.prepare()
        self.assertEqual(self.dump(), after)

    def test_non_demo_customer_is_refused_without_modification(self):
        self.prepare()
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT INTO customers(name,store_id) VALUES ('Private fictional test',1)")
        before = self.path.read_bytes()
        result = self.run_command('manage.py', 'prepare-render-demo')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Refusing non-demo', result.stdout)
        self.assertEqual(self.path.read_bytes(), before)

    def test_startup_stage_logs_are_flushed_and_restart_skips_work(self):
        result = self.run_command('manage.py', 'prepare-render-demo')
        self.assertEqual(result.returncode, 0, result.stderr)
        for stage in ('validating demo configuration', 'bootstrap lock acquired',
                      'schema initialization complete', 'fictional demo seed complete',
                      'ready; bootstrap lock released; exiting successfully'):
            self.assertIn(stage, result.stdout)
        self.assertNotIn(self.env['TCCP_SECRET_KEY'], result.stdout + result.stderr)
        result = self.run_command('manage.py', 'prepare-render-demo')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('migrations skipped', result.stdout)
        self.assertIn('seeding skipped', result.stdout)

    def test_bootstrap_lock_contention_times_out_without_database_changes(self):
        import fcntl

        self.prepare()
        before = self.path.read_bytes()
        with open(self.path.parent / '.bootstrap.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = self.run_command('-c', '''
from manage import prepare_render_demo
try:
 prepare_render_demo(lock_timeout=0.15)
except TimeoutError:
 raise SystemExit(1)
''')
        self.assertEqual(result.returncode, 1)
        self.assertIn('waiting for bootstrap lock', result.stdout)
        self.assertNotIn('bootstrap lock acquired', result.stdout)
        self.assertEqual(self.path.read_bytes(), before)

    def test_locked_backup_has_deadline_and_preserves_source(self):
        self.prepare()
        before = self.path.read_bytes()
        backup = self.path.parent / 'contended.backup'
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute('BEGIN EXCLUSIVE')
            try:
                result = self.run_command('-c', '''
from manage import backup_database
import os
from pathlib import Path
try:
 backup_database(Path(os.environ['TCCP_DB_PATH']).with_name('contended.backup'), timeout=0.15)
except TimeoutError:
 raise SystemExit(1)
''')
            finally:
                conn.rollback()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(backup.exists())  # Retain incomplete evidence; never replace the source.

    def test_explicit_wsgi_target_is_required(self):
        self.prepare()
        result = self.run_command('-m', 'gunicorn', '--check-config', 'web_app')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('application', result.stderr)
        result = self.run_command('-m', 'gunicorn', '--check-config', 'web_app:app')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('application ready', result.stderr)

    def test_render_shell_command_binds_assigned_port_and_passes_health_check(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        env = dict(self.env, PORT=str(port), TCCP_TEST_PYTHON=sys.executable)
        command = ('"$TCCP_TEST_PYTHON" -u manage.py prepare-render-demo && '
                   'exec "$TCCP_TEST_PYTHON" -m gunicorn --bind 0.0.0.0:$PORT '
                   '--workers 1 --threads 2 --timeout 60 --error-logfile - web_app:app')
        started = time.monotonic()
        with tempfile.TemporaryFile(mode='w+') as log:
            process = subprocess.Popen(['/bin/sh', '-c', command], env=env, stdout=log, stderr=log)
            try:
                response_status = None
                while time.monotonic() - started < 20 and process.poll() is None:
                    try:
                        with closing(http.client.HTTPConnection('127.0.0.1', port, timeout=2)) as client:
                            client.request('GET', '/login', headers={'Host':'localhost', 'Connection':'close'})
                            response = client.getresponse()
                            response_status = response.status
                            response.read()
                        if response_status == 200:
                            break
                    except (OSError, http.client.HTTPException):
                        pass
                    time.sleep(0.05)
                ready = time.monotonic() - started
                log.seek(0)
                self.assertEqual(response_status, 200, log.read())
                for path, host, expected in (('/login','wrong.invalid',400), ('/health','localhost',404)):
                    with closing(http.client.HTTPConnection('127.0.0.1', port, timeout=3)) as client:
                        client.request('GET', path, headers={'Host':host, 'Connection':'close'})
                        response = client.getresponse()
                        self.assertEqual(response.status, expected)
                        response.read()
            finally:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            log.seek(0)
            output = log.read()
        self.assertIn(f'Listening at: http://0.0.0.0:{port}', output)
        self.assertIn('application ready', output)
        self.assertIn('bootstrap lock released; exiting successfully', output)
        self.assertNotIn(self.env['TCCP_SECRET_KEY'], output)
        print(f'Fresh bootstrap + Gunicorn TCP /login ready in {ready:.3f}s; wrong Host=400; /health=404.')
