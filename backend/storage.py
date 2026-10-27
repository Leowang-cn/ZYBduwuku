"""Private COS or MinIO storage; database keys remain provider-independent."""
import datetime
import os
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import HTTPException
from fastapi.responses import RedirectResponse


_SETTINGS = {
    "cos": ("COS_REGION", "COS_BUCKET", "COS_SECRET_ID", "COS_SECRET_KEY", "COS_PREFIX"),
    "minio": ("MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_BUCKET"),
}


def _provider() -> str:
    provider = os.getenv("STORAGE_PROVIDER", "minio").strip().lower()
    if provider not in _SETTINGS:
        raise RuntimeError("STORAGE_PROVIDER must be cos or minio")
    return provider


def _prefix() -> str:
    prefix = os.getenv("COS_PREFIX", "").strip().strip("/")
    if not prefix or any(part in ("", ".", "..") for part in prefix.split("/")) or "\\" in prefix:
        raise RuntimeError("COS_PREFIX must be a non-empty directory prefix without traversal")
    return prefix + "/"


def configured() -> bool:
    try:
        require_configuration()
    except RuntimeError:
        return False
    return True


def require_configuration() -> None:
    provider = _provider()
    missing = [setting for setting in _SETTINGS[provider] if not os.getenv(setting, "").strip()]
    if missing:
        raise RuntimeError(f"{provider.upper()} configuration is required: {', '.join(missing)}")
    if provider == "cos":
        _prefix()


def _object_key(key: str) -> str:
    if not key or key.startswith("/") or "\\" in key or any(part in ("", ".", "..") for part in key.split("/")):
        raise ValueError("Invalid object key")
    return _prefix() + key


def _cos_client():
    require_configuration()
    from qcloud_cos import CosConfig, CosS3Client

    return CosS3Client(CosConfig(
        Region=os.environ["COS_REGION"].strip(),
        SecretId=os.environ["COS_SECRET_ID"].strip(),
        SecretKey=os.environ["COS_SECRET_KEY"].strip(),
        Token=os.getenv("COS_SESSION_TOKEN") or None,
        Scheme="https",
    ))


def _client():
    require_configuration()
    from minio import Minio

    endpoint = os.environ["MINIO_ENDPOINT"].strip()
    parsed = urlparse(endpoint if "://" in endpoint else "http://" + endpoint)
    return Minio(
        parsed.netloc,
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=parsed.scheme == "https" or os.getenv("MINIO_SECURE", "false").lower() == "true",
    )


def upload(key: str, path: Path, content_type: str) -> None:
    if _provider() == "cos":
        client = _cos_client()
        client.upload_file(
            Bucket=os.environ["COS_BUCKET"].strip(),
            Key=_object_key(key),
            LocalFilePath=str(path),
            ContentType=content_type,
            ACL="private",
        )
        return
    client = _client()
    bucket = os.environ["MINIO_BUCKET"]
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    try:
        policy = client.get_bucket_policy(bucket)
    except Exception as exc:
        if getattr(exc, "code", "") != "NoSuchBucketPolicy":
            raise
    else:
        if policy:
            raise RuntimeError("MinIO bucket must have no public policy")
    client.fput_object(bucket, key, str(path), content_type=content_type)


def delete(key: str) -> None:
    """Remove a private object from the configured provider."""
    if _provider() == "cos":
        _cos_client().delete_object(Bucket=os.environ["COS_BUCKET"].strip(), Key=_object_key(key))
        return
    _client().remove_object(os.environ["MINIO_BUCKET"], key)
def serve(key: str, filename: str, content_type: str, *, attachment: bool = True):
    headers = {
        "response-content-disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}" if attachment else "inline",
        "response-content-type": content_type,
    }
    if _provider() == "cos":
        url = _cos_client().get_presigned_url(
            Method="GET",
            Bucket=os.environ["COS_BUCKET"].strip(),
            Key=_object_key(key),
            Expired=300,
            Params=headers,
        )
        return RedirectResponse(url)
    client = _client()
    url = client.presigned_get_object(
        os.environ["MINIO_BUCKET"],
        key,
        expires=datetime.timedelta(minutes=5),
        response_headers=headers,
    )
    return RedirectResponse(url)
