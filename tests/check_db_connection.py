"""Check the Supabase Session-pooler connection (SPEC.md step 2).

Usage (from the repo root):
    python tests/check_db_connection.py                  # uses DATABASE_URL (admin)
    python tests/check_db_connection.py POWERBI_DATABASE_URL   # any other env var, e.g. powerbi_reader

Not collected by pytest (filename does not start with test_). Never prints the password.
"""
import os
import sys
import time

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

MAX_ATTEMPTS = 3  # SPEC section 9: DB connection errors retried up to 3 times (DB may be waking up)


def main() -> int:
    env_name = sys.argv[1] if len(sys.argv) > 1 else "DATABASE_URL"
    load_dotenv()
    raw = os.getenv(env_name)
    if not raw:
        print(f"ERROR: {env_name} is not set (check .env).")
        return 1

    url = make_url(raw)
    warnings = []
    # An unencoded '@', ']' etc. in the password makes the URL parse wrongly and the host then
    # contains part of the password. Detect it and never print that host.
    if not url.host or any(c in url.host for c in "@]:/+[ "):
        print("ERROR: DATABASE_URL is malformed. The password probably has special characters "
              "(@ : / + [ ] # ? %) that must be URL-encoded (e.g. @ -> %40), or reset it to letters/digits only.")
        return 1
    print(f"Target : {url.username}@{url.host}:{url.port}/{url.database}")

    if url.host and url.host.startswith("db.") and url.host.endswith(".supabase.co"):
        warnings.append("Host looks like the DIRECT connection (IPv6-only). Use the Session pooler host.")
    if url.port == 6543:
        warnings.append("Port 6543 is the transaction pooler. Use 5432 (Session pooler).")
    if url.query.get("sslmode") != "require":
        warnings.append("sslmode=require is missing from the URL.")
    if url.username and "." not in url.username:
        warnings.append("Username has no '.<project_ref>' suffix; the pooler needs it.")
    for w in warnings:
        print(f"WARNING: {w}")

    engine = create_engine(url)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with engine.connect() as conn:
                row = conn.execute(text("SELECT current_user, version()")).one()
                print(f"User   : {row[0]}")
                print(f"Server : {row[1].split(',')[0]}")
                # Client -> pooler SSL. (pg_stat_ssl would show the pooler -> Postgres hop instead.)
                print(f"SSL    : {conn.connection.dbapi_connection.pgconn.ssl_in_use}")

                tables = conn.execute(text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'plave' ORDER BY table_name"
                )).scalars().all()
                print(f"Schema plave tables ({len(tables)}): {', '.join(tables) or '(none, run sql/01_ddl.sql)'}")

                schema_exists = conn.execute(text(
                    "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'plave')"
                )).scalar()
                if not schema_exists:
                    print("Schema plave does not exist yet: run sql/01_ddl.sql, 02_seed, 03_roles.sql.")
                    print("OK: connection works.")
                    return 0
                can_use = conn.execute(text(
                    "SELECT has_schema_privilege('plave', 'USAGE')"
                )).scalar()
                print(f"USAGE on plave: {can_use}")
                for t in ("dim_category", "dim_date"):
                    if t in tables:
                        n = conn.execute(text(f"SELECT count(*) FROM plave.{t}")).scalar()
                        print(f"  plave.{t}: {n} rows")
            print("OK: connection works.")
            return 0
        except OperationalError as exc:
            msg = str(exc.orig or exc).splitlines()[0]
            print(f"Attempt {attempt}/{MAX_ATTEMPTS} failed: {msg}")
            # Auth errors are not transient; retrying only risks Supabase's IP ban for repeated failures.
            if "password authentication failed" in msg or "Tenant or user not found" in msg:
                print("FAILED: wrong username/password. Check the database password (not your Supabase account password).")
                return 1
            if attempt < MAX_ATTEMPTS:
                time.sleep(2 ** attempt)
    print("FAILED: could not connect.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
