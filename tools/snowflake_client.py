"""
Snowflake client using RSA key pair authentication.
Credentials are pulled from /cybertronics/snowflake in Secrets Manager.
Never use username/password auth — RSA key pair only.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

import snowflake.connector
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from tools.secrets import get_secret_cached

# Snowflake roles — maps to roles defined in infra/snowflake/schema-setup.sql
ROLE_LOADER      = "AGENT_LOADER"       # write to RAW schema
ROLE_TRANSFORMER = "AGENT_TRANSFORMER"  # dbt work: read RAW, write STAGING/INTERMEDIATE/MARTS
ROLE_ANALYST     = "AGENT_ANALYST"      # read-only on MARTS + SMITH_TEST


def _private_key_bytes(pem: str) -> bytes:
    """Convert PEM string to DER bytes for snowflake-connector."""
    key = load_pem_private_key(pem.encode(), password=None, backend=default_backend())
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def get_connection(role: str = ROLE_TRANSFORMER, schema: str | None = None):
    """
    Open a Snowflake connection.

    Args:
        role:   Snowflake role to use (use ROLE_* constants above).
        schema: Override the default schema (useful for dev/test work).

    Returns:
        snowflake.connector.SnowflakeConnection — caller is responsible for closing.
    """
    creds = get_secret_cached("/cybertronics/snowflake")
    return snowflake.connector.connect(
        account=creds["account"],
        user=creds["user"],
        private_key=_private_key_bytes(creds["private_key"]),
        warehouse=creds["warehouse"],
        database=creds["database"],
        role=role,
        schema=schema,
    )


@contextmanager
def connection(role: str = ROLE_TRANSFORMER, schema: str | None = None) -> Generator:
    """Context manager that auto-closes the connection."""
    conn = get_connection(role=role, schema=schema)
    try:
        yield conn
    finally:
        conn.close()


def execute(sql: str, role: str = ROLE_TRANSFORMER, schema: str | None = None) -> list[tuple]:
    """Run a single SQL statement and return all rows."""
    with connection(role=role, schema=schema) as conn:
        cur = conn.cursor()
        cur.execute(sql)
        return cur.fetchall()


def execute_many(statements: list[str], role: str = ROLE_TRANSFORMER, schema: str | None = None) -> None:
    """Run multiple SQL statements in sequence on one connection."""
    with connection(role=role, schema=schema) as conn:
        cur = conn.cursor()
        for sql in statements:
            cur.execute(sql)


def copy_from_stage(
    table: str,
    stage: str = "@RAW.s3_raw_csv",
    pattern: str | None = None,
    purge: bool = False,
    role: str = ROLE_LOADER,
) -> dict:
    """
    Run COPY INTO <table> FROM <stage>.

    Args:
        table:   Fully-qualified table name, e.g. 'RAW.my_source_table'
        stage:   Snowflake stage reference, e.g. '@RAW.s3_raw_csv/path/to/files/'
        pattern: Optional regex pattern to filter files
        purge:   Remove files from stage after successful load

    Returns:
        dict with keys: files_loaded, rows_loaded, errors
    """
    pattern_clause = f"PATTERN = '{pattern}'" if pattern else ""
    purge_clause   = "PURGE = TRUE" if purge else ""
    sql = f"""
        COPY INTO {table}
        FROM {stage}
        {pattern_clause}
        {purge_clause}
        ON_ERROR = CONTINUE
    """
    rows = execute(sql.strip(), role=role)
    return {
        "files_loaded": len(rows),
        "rows_loaded":  sum(r[3] for r in rows if r[3]),
        "errors":       [r for r in rows if r[1] != "LOADED"],
    }
