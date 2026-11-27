"""Run the suite against a real PostgreSQL 16 (embedded via pgserver) instead of SQLite."""
import os, subprocess, sys, tempfile
import pgserver
d = tempfile.mkdtemp()
srv = pgserver.get_server(d)
uri = srv.get_uri().replace("postgresql://", "postgresql+psycopg2://")
env = {**os.environ, "VGOH_TEST_DATABASE_URL": uri}
r = subprocess.run([sys.executable, "-m", "pytest", "-x", "-q", *sys.argv[1:]], env=env, cwd=os.path.dirname(os.path.dirname(__file__)))
sys.exit(r.returncode)
