from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os

from app.backend.db.database_url import normalize_database_url

DATABASE_URL, _is_postgres = normalize_database_url(os.getenv("DATABASE_URL", "./resume_screener.db"))

# Pool settings only for PostgreSQL (SQLite doesn't support pool settings).
# Sizes are env-configurable so multi-worker deployments can keep the total
# connection count (workers × pool_size) under PostgreSQL max_connections.
# Defaults are conservative per-worker; tune DATABASE_POOL_SIZE via env.
_pool_kwargs = {}
if _is_postgres:
    _pool_kwargs = {
        "pool_size": int(os.getenv("DATABASE_POOL_SIZE", "5")),
        "max_overflow": int(os.getenv("DATABASE_MAX_OVERFLOW", "10")),
        "pool_recycle": int(os.getenv("DATABASE_POOL_RECYCLE", "3600")),
        "pool_timeout": int(os.getenv("DATABASE_POOL_TIMEOUT", "30")),
    }

connect_args = {"check_same_thread": False} if not _is_postgres else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    **_pool_kwargs
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

_replica_url = os.getenv("DATABASE_READ_REPLICA_URL", "").strip()
if _replica_url:
    _replica_url, _ = normalize_database_url(_replica_url)
if _replica_url:
    replica_engine = create_engine(_replica_url, pool_pre_ping=True, **_pool_kwargs)
    ReplicaSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=replica_engine)
else:
    ReplicaSessionLocal = SessionLocal

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_read_db():
    """Read-replica session when DATABASE_READ_REPLICA_URL is set, else primary."""
    db = ReplicaSessionLocal()
    try:
        yield db
    finally:
        db.close()
