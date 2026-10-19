"""Browser uploads go to the bucket's regional address, never the global one."""

from __future__ import annotations

import os
import unittest
from unittest import mock

import _path  # noqa: F401

try:
    import boto3  # noqa: F401
except ImportError:  # pragma: no cover
    boto3 = None


@unittest.skipIf(boto3 is None, "boto3 not installed")
class RegionalEndpointTests(unittest.TestCase):
    def _adapter(self, env: dict):
        from omni_modal.saas.adapters.storage import S3StorageAdapter  # noqa: PLC0415

        base = {"S3_BUCKET": "omero-ai", "AWS_ACCESS_KEY_ID": "AKIATEST", "AWS_SECRET_ACCESS_KEY": "secret"}
        with mock.patch.dict(os.environ, {**base, **env}, clear=False):
            for name in ("S3_REGION", "AWS_REGION", "AWS_DEFAULT_REGION", "S3_ENDPOINT_URL"):
                if name not in env:
                    os.environ.pop(name, None)
            return S3StorageAdapter()

    def test_regional_upload_url(self) -> None:
        post = self._adapter({"S3_REGION": "ap-south-1"}).presigned_upload("k.incoming", 10, "application/pdf")
        self.assertEqual(post["url"], "https://omero-ai.s3.ap-south-1.amazonaws.com/")
        self.assertEqual(post["fields"].get("x-amz-algorithm"), "AWS4-HMAC-SHA256")

    def test_aws_region_variable_also_works(self) -> None:
        post = self._adapter({"AWS_REGION": "eu-west-1"}).presigned_upload("k.incoming", 10, "application/pdf")
        self.assertEqual(post["url"], "https://omero-ai.s3.eu-west-1.amazonaws.com/")

    def test_custom_endpoint_is_kept(self) -> None:
        post = self._adapter({"S3_ENDPOINT_URL": "http://localhost:9000", "S3_REGION": "us-east-1"}).presigned_upload(
            "k.incoming", 10, "application/pdf")
        self.assertTrue(post["url"].startswith("http://localhost:9000"))


if __name__ == "__main__":
    unittest.main()
