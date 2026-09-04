"""Signed file delivery owned by the System."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
from pydantic import ValidationError

from n4x.contracts.file_delivery import (
    FILE_DELIVERY_CONTRACT_VERSION,
    FILE_DELIVERY_ENVELOPE_KEY,
    FILE_DELIVERY_REQUESTS_KEY,
    FILE_DELIVERY_SIGNING_KEY_ENV,
    FileDeliveryRequest,
)
from n4x.kernel.errors import FileDeliveryError
from n4x.kernel.paths import PathContainmentError, resolve_path_within
from n4x.runtime.actions import RuntimePaths

_SIGNING_KEY_BYTES = 32
_MAX_TOKEN_LENGTH = 16_384


@dataclass(frozen=True)
class DeliveredFile:
    path: Path
    content_type: str
    disposition: str
    filename: str | None


class FileDeliveryService:
    """Confines and signs generic Experience delivery of app-owned files."""

    def __init__(
        self, runtime_paths: RuntimePaths, *, signing_key: bytes | None = None
    ) -> None:
        self.paths = runtime_paths
        self.signing_key = signing_key or self._load_or_create_signing_key()
        self.cipher = Fernet(base64.urlsafe_b64encode(self.signing_key))

    def prepare_output(
        self,
        output: Any,
        *,
        application_id: str,
        experience_id: str,
        experience_revision_id: str,
        data_space_id: str = "production",
        deployment_id: str | None = None,
    ) -> Any:
        if not isinstance(output, dict) or FILE_DELIVERY_ENVELOPE_KEY not in output:
            return output
        if FILE_DELIVERY_REQUESTS_KEY in output:
            self._fail_invalid_request()

        envelope = output[FILE_DELIVERY_ENVELOPE_KEY]
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {FILE_DELIVERY_REQUESTS_KEY}
            or not isinstance(envelope[FILE_DELIVERY_REQUESTS_KEY], list)
        ):
            self._fail_invalid_request()

        try:
            requests = [
                FileDeliveryRequest.model_validate(item)
                for item in envelope[FILE_DELIVERY_REQUESTS_KEY]
            ]
        except ValidationError as exc:
            raise FileDeliveryError(
                "invalid_file_delivery",
                "action returned an invalid file delivery descriptor",
                502,
            ) from exc

        public_output = dict(output)
        public_output.pop(FILE_DELIVERY_ENVELOPE_KEY)
        if requests:
            public_output[FILE_DELIVERY_REQUESTS_KEY] = [
                self._issue(
                    request,
                    application_id=application_id,
                    experience_id=experience_id,
                    experience_revision_id=experience_revision_id,
                    data_space_id=data_space_id,
                    deployment_id=deployment_id,
                )
                for request in requests
            ]
        return public_output

    @staticmethod
    def strip_delivery_fields(output: Any) -> Any:
        if not isinstance(output, dict):
            return output
        stripped = dict(output)
        stripped.pop(FILE_DELIVERY_ENVELOPE_KEY, None)
        stripped.pop(FILE_DELIVERY_REQUESTS_KEY, None)
        return stripped

    def resolve(
        self,
        token: str,
        *,
        application_id: str,
        experience_id: str,
        experience_revision_id: str,
        data_space_id: str = "production",
        deployment_id: str | None = None,
    ) -> DeliveredFile:
        payload = self._decode(token)
        now = int(time.time())
        if payload["expires_at"] < now:
            raise FileDeliveryError(
                "file_delivery_expired", "file delivery has expired", 410
            )

        scope_key = self.paths.application_data_scope_key(
            application_id, data_space_id
        )
        expected_scope = self.paths.application_data_scope_id(scope_key)
        if (
            payload["application_id"] != application_id
            or payload["experience_id"] != experience_id
            or payload["experience_revision_id"] != experience_revision_id
            or payload["data_scope_id"] != expected_scope
            or payload.get("data_space_id", "production") != data_space_id
            or payload.get("deployment_id") != deployment_id
        ):
            raise FileDeliveryError(
                "file_delivery_scope_mismatch",
                "file delivery is not valid for this scope",
                403,
            )

        root = self.paths.application_data_root(scope_key)
        try:
            path = resolve_path_within(root, payload["path"])
        except PathContainmentError as exc:
            raise FileDeliveryError(
                "invalid_file_delivery", "file delivery path is invalid", 400
            ) from exc
        if not path.is_file():
            raise FileDeliveryError(
                "file_delivery_not_found", "delivered file was not found", 404
            )
        return DeliveredFile(
            path=path,
            content_type=payload["content_type"],
            disposition=payload["disposition"],
            filename=payload["filename"],
        )

    def _issue(
        self,
        request: FileDeliveryRequest,
        *,
        application_id: str,
        experience_id: str,
        experience_revision_id: str,
        data_space_id: str,
        deployment_id: str | None,
    ) -> dict[str, Any]:
        scope_key = self.paths.application_data_scope_key(
            application_id, data_space_id
        )
        root = self.paths.application_data_root(scope_key)
        try:
            path = resolve_path_within(root, request.path)
        except PathContainmentError as exc:
            raise FileDeliveryError(
                "invalid_file_delivery",
                "action requested an invalid file delivery path",
                502,
            ) from exc
        if not path.is_file():
            raise FileDeliveryError(
                "invalid_file_delivery",
                "action requested delivery of a missing file",
                502,
            )

        expires_at = int(time.time()) + request.expires_in_seconds
        payload = {
            "version": FILE_DELIVERY_CONTRACT_VERSION,
            "data_scope_id": self.paths.application_data_scope_id(scope_key),
            "application_id": application_id,
            "data_space_id": data_space_id,
            "deployment_id": deployment_id,
            "experience_id": experience_id,
            "experience_revision_id": experience_revision_id,
            "path": request.path,
            "content_type": request.content_type,
            "disposition": request.disposition,
            "filename": request.filename,
            "expires_at": expires_at,
        }
        token = self._encode(payload)
        url = (
            f"/api/experiences/{quote(experience_id, safe='')}/apps/"
            f"{quote(application_id, safe='')}/files/{token}"
            if deployment_id is None
            else (
                f"/api/development/{quote(deployment_id, safe='')}/apps/"
                f"{quote(application_id, safe='')}/files/{token}"
            )
        )
        return {
            "url": url,
            "content_type": request.content_type,
            "disposition": request.disposition,
            "filename": request.filename,
            "expires_at": datetime.fromtimestamp(expires_at, tz=UTC).isoformat(),
        }

    def _encode(self, payload: dict[str, Any]) -> str:
        return self.cipher.encrypt(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")

    def _decode(self, token: str) -> dict[str, Any]:
        if not isinstance(token, str) or len(token) > _MAX_TOKEN_LENGTH:
            self._fail_invalid_token()
        try:
            payload = json.loads(
                self.cipher.decrypt(token.encode("ascii")).decode("utf-8")
            )
        except (
            InvalidToken,
            UnicodeEncodeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise FileDeliveryError(
                "invalid_file_delivery_token",
                "file delivery token is invalid",
                404,
            ) from exc

        required = {
            "version": str,
            "data_scope_id": str,
            "application_id": str,
            "experience_id": str,
            "experience_revision_id": str,
            "path": str,
            "content_type": str,
            "disposition": str,
            "expires_at": int,
        }
        if (
            not isinstance(payload, dict)
            or any(
                not isinstance(payload.get(key), value_type)
                for key, value_type in required.items()
            )
            or payload.get("version") != FILE_DELIVERY_CONTRACT_VERSION
            or payload.get("disposition") not in {"inline", "attachment"}
            or (
                payload.get("filename") is not None
                and not isinstance(payload.get("filename"), str)
            )
        ):
            self._fail_invalid_token()
        return payload

    def _load_or_create_signing_key(self) -> bytes:
        configured = os.getenv(FILE_DELIVERY_SIGNING_KEY_ENV)
        if configured is not None and configured.strip():
            if len(configured) < 32:
                raise RuntimeError(
                    f"{FILE_DELIVERY_SIGNING_KEY_ENV} must contain at least 32 characters"
                )
            return hashlib.sha256(configured.encode("utf-8")).digest()

        key_path = self.paths.root / "config" / "file-delivery.key"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(key_path, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
        except FileExistsError:
            encoded = key_path.read_text(encoding="ascii").strip()
            key = self._b64decode(encoded)
            if len(key) != _SIGNING_KEY_BYTES:
                raise RuntimeError("file delivery signing key is invalid")
            return key

        key = secrets.token_bytes(_SIGNING_KEY_BYTES)
        with os.fdopen(fd, "w", encoding="ascii") as file:
            file.write(self._b64encode(key))
        return key

    @staticmethod
    def _b64encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _b64decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)

    @staticmethod
    def _fail_invalid_request() -> None:
        raise FileDeliveryError(
            "invalid_file_delivery",
            "action returned an invalid file delivery descriptor",
            502,
        )

    @staticmethod
    def _fail_invalid_token() -> None:
        raise FileDeliveryError(
            "invalid_file_delivery_token",
            "file delivery token is invalid",
            404,
        )
