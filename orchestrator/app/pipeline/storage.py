"""Cloudflare R2 image storage via S3-compatible API.

Images are stored after in-memory resize (max 1920px, JPEG 85%).
Full-resolution bytes are never persisted — only the resized copy is uploaded.
"""

from __future__ import annotations

import asyncio
import io
import logging
import uuid

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image

from app.config import settings

logger = logging.getLogger(__name__)


def _get_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )


def resize_image(image_bytes: bytes) -> bytes:
    """Resize image to max 1920px on longest side at JPEG quality 85%.

    Returns JPEG bytes. Input can be any Pillow-supported format.
    """
    with Image.open(io.BytesIO(image_bytes)) as img:
        img = img.convert("RGB")  # ensure JPEG-compatible mode

        max_px = settings.image_max_px
        w, h = img.size
        if max(w, h) > max_px:
            scale = max_px / max(w, h)
            new_w = int(w * scale)
            new_h = int(h * scale)
            img = img.resize((new_w, new_h), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=settings.image_jpeg_quality, optimize=True)
        return buf.getvalue()


def _upload_image_sync(image_bytes: bytes, group_id: str, message_id: str) -> str:
    """Sync inner implementation — call via asyncio.to_thread."""
    resized = resize_image(image_bytes)

    # Key: invoices/<group_id>/<uuid>.jpg (avoids exposing message IDs externally)
    key = f"invoices/{group_id}/{uuid.uuid4()}.jpg"

    try:
        client = _get_client()
        client.put_object(
            Bucket=settings.r2_bucket,
            Key=key,
            Body=resized,
            ContentType="image/jpeg",
        )
        logger.info("Uploaded image to R2: %s (%d bytes)", key, len(resized))
        return key
    except (BotoCoreError, ClientError) as exc:
        logger.error("R2 upload failed for key %s: %s", key, exc)
        raise RuntimeError(f"R2 upload failed: {exc}") from exc


async def upload_image(image_bytes: bytes, group_id: str, message_id: str) -> str:
    """Resize image and upload to R2. Returns the R2 object key.

    Raises RuntimeError if upload fails.
    """
    return await asyncio.to_thread(_upload_image_sync, image_bytes, group_id, message_id)


def download_image_sync(r2_key: str) -> bytes:
    """Sync download of image bytes from R2. Use as a callback in sync contexts (e.g. PDF builder).

    Raises RuntimeError if download fails.
    """
    try:
        client = _get_client()
        response = client.get_object(Bucket=settings.r2_bucket, Key=r2_key)
        return response["Body"].read()
    except (BotoCoreError, ClientError) as exc:
        logger.error("R2 download failed for key %s: %s", r2_key, exc)
        raise RuntimeError(f"R2 download failed: {exc}") from exc


async def download_image(r2_key: str) -> bytes:
    """Async download of image bytes from R2. Wraps the sync boto3 call in a thread.

    Raises RuntimeError if download fails.
    """
    return await asyncio.to_thread(download_image_sync, r2_key)


def _upload_metadata_sync(r2_key: str, metadata: dict) -> str:
    """Upload invoice metadata as a JSON sidecar alongside the image.

    The sidecar key is the image key with .jpg replaced by .json.
    Returns the sidecar key.
    """
    import json
    sidecar_key = r2_key.rsplit(".", 1)[0] + ".json"
    payload = json.dumps(metadata, default=str).encode("utf-8")
    try:
        client = _get_client()
        client.put_object(
            Bucket=settings.r2_bucket,
            Key=sidecar_key,
            Body=payload,
            ContentType="application/json",
        )
        logger.info("Uploaded metadata sidecar to R2: %s", sidecar_key)
        return sidecar_key
    except (BotoCoreError, ClientError) as exc:
        logger.error("R2 metadata upload failed for key %s: %s", sidecar_key, exc)
        raise RuntimeError(f"R2 metadata upload failed: {exc}") from exc


async def upload_metadata(r2_key: str, metadata: dict) -> str:
    """Async upload of invoice metadata JSON sidecar to R2.

    Returns the sidecar key. Raises RuntimeError if upload fails.
    """
    return await asyncio.to_thread(_upload_metadata_sync, r2_key, metadata)
