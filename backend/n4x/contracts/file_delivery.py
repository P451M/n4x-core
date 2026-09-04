from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

FILE_DELIVERY_CONTRACT_VERSION = "n4x.file.delivery.v1"
FILE_DELIVERY_ENVELOPE_KEY = "_n4x"
FILE_DELIVERY_REQUESTS_KEY = "file_deliveries"
FILE_DELIVERY_DEFAULT_TTL_SECONDS = 300
FILE_DELIVERY_MAX_TTL_SECONDS = 3600
FILE_DELIVERY_SIGNING_KEY_ENV = "N4X_FILE_DELIVERY_SIGNING_KEY"


class FileDeliveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)
    content_type: str = Field(
        default="application/octet-stream", min_length=1, max_length=255
    )
    disposition: Literal["inline", "attachment"] = "attachment"
    filename: str | None = Field(default=None, min_length=1, max_length=1024)
    expires_in_seconds: int = Field(
        default=FILE_DELIVERY_DEFAULT_TTL_SECONDS,
        ge=1,
        le=FILE_DELIVERY_MAX_TTL_SECONDS,
    )

    @field_validator("content_type", "filename")
    @classmethod
    def reject_header_control_characters(cls, value: str | None) -> str | None:
        if value is not None and ("\r" in value or "\n" in value or "\x00" in value):
            raise ValueError("HTTP metadata contains unsupported control characters")
        return value


FILE_DELIVERY_CONTRACT_SCHEMA = {
    "version": FILE_DELIVERY_CONTRACT_VERSION,
    "action_output": {
        "envelope": FILE_DELIVERY_ENVELOPE_KEY,
        "requests": FILE_DELIVERY_REQUESTS_KEY,
        "fields": [
            "path",
            "content_type",
            "disposition",
            "filename",
            "expires_in_seconds",
        ],
    },
    "ownership": {
        "path_layout": "Application",
        "reference_metadata": "Application",
        "path_confinement": "System",
        "http_delivery": "System",
    },
}
