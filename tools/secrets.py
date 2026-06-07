"""
AWS Secrets Manager client.
All other tool clients call this module to retrieve credentials at runtime.
Requires AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION in environment.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


def get_secret(secret_name: str) -> dict:
    """Retrieve and parse a JSON secret from AWS Secrets Manager."""
    client = boto3.client("secretsmanager", region_name=_REGION)
    try:
        response = client.get_secret_value(SecretId=secret_name)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ResourceNotFoundException":
            raise KeyError(f"Secret not found: {secret_name}") from e
        if code == "AccessDeniedException":
            raise PermissionError(
                f"No permission to read secret: {secret_name}. "
                "Check IAM policy on the cybertronics-agents user."
            ) from e
        raise
    return json.loads(response["SecretString"])


@lru_cache(maxsize=16)
def _cached_secret(secret_name: str) -> str:
    """Cache secrets for the process lifetime — avoid repeated Secrets Manager calls."""
    return json.dumps(get_secret(secret_name))


def get_secret_cached(secret_name: str) -> dict:
    """Cached version — use when the secret won't change during the session."""
    return json.loads(_cached_secret(secret_name))
