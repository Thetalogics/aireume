"""Hash auth verification and reset tokens.

Revision ID: 084_auth_token_hashes
Revises: 083_generation_mode
"""
from alembic import op
import hashlib
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "084_auth_token_hashes"
down_revision = "083_generation_mode"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, column: str) -> bool:
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "users" in tables and not _has_column(insp, "users", "email_verification_token_hash"):
        op.add_column("users", sa.Column("email_verification_token_hash", sa.String(64), nullable=True))
        op.create_index(
            "ix_users_email_verification_token_hash",
            "users",
            ["email_verification_token_hash"],
            unique=False,
        )
    if "users" in tables and _has_column(insp, "users", "email_verification_token_hash"):
        rows = bind.execute(sa.text(
            "SELECT id, email_verification_token FROM users "
            "WHERE email_verification_token IS NOT NULL "
            "AND email_verification_token_hash IS NULL"
        )).fetchall()
        for row in rows:
            token_hash = hashlib.sha256(row.email_verification_token.encode("utf-8")).hexdigest()
            bind.execute(sa.text(
                "UPDATE users SET email_verification_token_hash = :token_hash WHERE id = :id"
            ), {"token_hash": token_hash, "id": row.id})
    if "password_reset_tokens" in tables and not _has_column(insp, "password_reset_tokens", "token_hash"):
        op.add_column("password_reset_tokens", sa.Column("token_hash", sa.String(64), nullable=True))
        op.create_index(
            "ix_password_reset_tokens_token_hash",
            "password_reset_tokens",
            ["token_hash"],
            unique=True,
        )
    if "password_reset_tokens" in tables and _has_column(insp, "password_reset_tokens", "token_hash"):
        rows = bind.execute(sa.text(
            "SELECT id, token FROM password_reset_tokens "
            "WHERE token IS NOT NULL "
            "AND token_hash IS NULL"
        )).fetchall()
        for row in rows:
            token_hash = hashlib.sha256(row.token.encode("utf-8")).hexdigest()
            bind.execute(sa.text(
                "UPDATE password_reset_tokens SET token_hash = :token_hash WHERE id = :id"
            ), {"token_hash": token_hash, "id": row.id})


def downgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "password_reset_tokens" in tables and _has_column(insp, "password_reset_tokens", "token_hash"):
        op.drop_index("ix_password_reset_tokens_token_hash", table_name="password_reset_tokens")
        op.drop_column("password_reset_tokens", "token_hash")
    if "users" in tables and _has_column(insp, "users", "email_verification_token_hash"):
        op.drop_index("ix_users_email_verification_token_hash", table_name="users")
        op.drop_column("users", "email_verification_token_hash")
