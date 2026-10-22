import os
import sys
from dotenv import load_dotenv
import psycopg
import redis
import duckdb
from qdrant_client import QdrantClient

load_dotenv()

def check_postgres() -> None:
    conn_str = (
        f"dbname={os.getenv('POSTGRES_DB', 'aegisops_db')} "
        f"user={os.getenv('POSTGRES_USER', 'aegis_admin')} "
        f"password={os.getenv('POSTGRES_PASSWORD', 'aegis_secret_pwd')} "
        f"host={os.getenv('POSTGRES_HOST', 'localhost')} "
        f"port={os.getenv('POSTGRES_PORT', '5432')}"
    )
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version();")
            version = cur.fetchone()[0]
            print(f"[OK] PostgreSQL connected: {version.split(',')[0]}")

def check_redis() -> None:
    r = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        decode_responses=True,
    )
    pong = r.ping()
    print(f"[OK] Redis connected: PING -> {pong}")

def check_qdrant() -> None:
    client = QdrantClient(
        host=os.getenv("QDRANT_HOST", "localhost"),
        port=int(os.getenv("QDRANT_PORT", 6333)),
    )
    collections = client.get_collections()
    print(f"[OK] Qdrant connected: {len(collections.collections)} existing collections")

def check_duckdb() -> None:
    db_path = os.getenv("DUCKDB_PATH", "data/telemetry_parquet/aegis_telemetry.duckdb")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = duckdb.connect(db_path)
    version = con.sql("SELECT version()").fetchone()[0]
    con.close()
    print(f"[OK] DuckDB initialized at '{db_path}': version {version}")

if __name__ == "__main__":
    print("=== AegisOps Infrastructure Healthcheck ===")
    try:
        check_postgres()
        check_redis()
        check_qdrant()
        check_duckdb()
        print("\nAll Phase 1.1 infrastructure services are UP and healthy!")
    except Exception as e:
        print(f"\n[ERROR] Infrastructure check failed: {e}", file=sys.stderr)
        sys.exit(1)