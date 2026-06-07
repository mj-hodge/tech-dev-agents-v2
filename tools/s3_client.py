"""
S3 client for the Cybertronics data lake.
Bucket name is pulled from /cybertronics/aws in Secrets Manager.
"""

from __future__ import annotations

import os
from pathlib import Path

import boto3

from tools.secrets import get_secret_cached

_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


def _bucket() -> str:
    return get_secret_cached("/cybertronics/aws")["bucket_name"]


def _client():
    return boto3.client("s3", region_name=_REGION)


def upload_file(local_path: str, s3_key: str, bucket: str | None = None) -> str:
    """Upload a local file to S3. Returns the s3:// URI."""
    b = bucket or _bucket()
    _client().upload_file(local_path, b, s3_key)
    return f"s3://{b}/{s3_key}"


def upload_bytes(data: bytes, s3_key: str, bucket: str | None = None) -> str:
    """Upload raw bytes to S3. Returns the s3:// URI."""
    b = bucket or _bucket()
    _client().put_object(Bucket=b, Key=s3_key, Body=data)
    return f"s3://{b}/{s3_key}"


def download_file(s3_key: str, local_path: str, bucket: str | None = None) -> None:
    """Download an S3 object to a local path. Creates parent dirs if needed."""
    b = bucket or _bucket()
    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    _client().download_file(b, s3_key, local_path)


def read_bytes(s3_key: str, bucket: str | None = None) -> bytes:
    """Read an S3 object into memory."""
    b = bucket or _bucket()
    response = _client().get_object(Bucket=b, Key=s3_key)
    return response["Body"].read()


def list_keys(prefix: str, bucket: str | None = None) -> list[str]:
    """List all object keys under a prefix."""
    b = bucket or _bucket()
    paginator = _client().get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=b, Prefix=prefix):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


def key_exists(s3_key: str, bucket: str | None = None) -> bool:
    """Return True if the S3 key exists."""
    b = bucket or _bucket()
    try:
        _client().head_object(Bucket=b, Key=s3_key)
        return True
    except _client().exceptions.ClientError:
        return False


def delete_key(s3_key: str, bucket: str | None = None) -> None:
    """Delete a single S3 object."""
    b = bucket or _bucket()
    _client().delete_object(Bucket=b, Key=s3_key)


def raw_prefix(source: str, year: str, month: str, day: str) -> str:
    """Standard S3 key prefix for raw data: raw/{source}/{YYYY}/{MM}/{DD}/"""
    return f"raw/{source}/{year}/{month}/{day}/"
