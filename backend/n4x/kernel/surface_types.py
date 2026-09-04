from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SurfaceCspConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connect_domains: list[str] = Field(default_factory=list)
    resource_domains: list[str] = Field(default_factory=list)

    @field_validator("connect_domains", "resource_domains")
    @classmethod
    def validate_origins(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("CSP origins must be unique")
        for value in values:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
                or "*" in value
            ):
                raise ValueError(f"invalid absolute CSP origin: {value}")
        return values


DEFAULT_PWA_MANIFEST_PATH = "manifest.webmanifest"


class BrowserPwaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_path: str = DEFAULT_PWA_MANIFEST_PATH

    @field_validator("manifest_path")
    @classmethod
    def validate_manifest_path(cls, value: str) -> str:
        return _validate_artifact_relative_path(value, field_name="manifest_path")


class BrowserSurfaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mount_path: str = "/"
    build_profile: dict[str, Any] = Field(default_factory=dict)
    fallback: str | None = None
    csp: SurfaceCspConfig = Field(default_factory=SurfaceCspConfig)
    host_metadata: dict[str, Any] = Field(default_factory=dict)
    pwa: BrowserPwaConfig | None = None

    @field_validator("mount_path")
    @classmethod
    def validate_mount_path(cls, value: str) -> str:
        return _validate_absolute_path(value, field_name="mount_path")

    @field_validator("fallback")
    @classmethod
    def validate_fallback(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_absolute_path(value, field_name="fallback")

    @model_validator(mode="after")
    def validate_pwa_mount(self) -> BrowserSurfaceConfig:
        if self.pwa is not None and self.mount_path != "/":
            raise ValueError("pwa is valid only on the / browser Surface")
        return self


class McpAppSurfaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metadata: dict[str, Any] = Field(default_factory=dict)
    related_browser_path: str | None = None
    csp: SurfaceCspConfig = Field(default_factory=SurfaceCspConfig)
    host_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("related_browser_path")
    @classmethod
    def validate_related_browser_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_absolute_path(value, field_name="related_browser_path")


SurfaceConfigValidator = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class SurfaceTypeDefinition:
    surface_type: str
    version: int
    validate_config: SurfaceConfigValidator


class SurfaceTypeRegistry:
    """Small internal registry for versioned Surface contract definitions."""

    def __init__(self) -> None:
        self._definitions: dict[tuple[str, int], SurfaceTypeDefinition] = {}

    def register(self, definition: SurfaceTypeDefinition) -> None:
        key = (definition.surface_type, definition.version)
        if not definition.surface_type.strip():
            raise ValueError("surface_type must not be empty")
        if definition.version < 1:
            raise ValueError("surface_type version must be positive")
        if key in self._definitions:
            raise ValueError(
                f"Surface type already registered: {definition.surface_type}"
                f"@{definition.version}"
            )
        self._definitions[key] = definition

    def definition(self, surface_type: str, version: int) -> SurfaceTypeDefinition:
        try:
            return self._definitions[(surface_type, version)]
        except KeyError as error:
            raise ValueError(
                f"unsupported Surface type: {surface_type}@{version}"
            ) from error

    def validate_config(
        self, surface_type: str, version: int, config: dict[str, Any]
    ) -> dict[str, Any]:
        return self.definition(surface_type, version).validate_config(config)


def _model_validator(model_type: type[BaseModel]) -> SurfaceConfigValidator:
    def validate(config: dict[str, Any]) -> dict[str, Any]:
        return model_type.model_validate(config).model_dump(mode="json")

    return validate


def _browser_config_validator(config: dict[str, Any]) -> dict[str, Any]:
    dumped = BrowserSurfaceConfig.model_validate(config).model_dump(mode="json")
    if dumped.get("pwa") is None:
        dumped.pop("pwa", None)
    return dumped


def _validate_artifact_relative_path(value: str, *, field_name: str) -> str:
    if (
        not value
        or value.startswith("/")
        or value.startswith("\\")
        or "?" in value
        or "#" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError(
            f"{field_name} must be a traversal-safe artifact-relative path"
        )
    return value


def _validate_absolute_path(value: str, *, field_name: str) -> str:
    if (
        not value.startswith("/")
        or value.startswith("//")
        or "?" in value
        or "#" in value
        or any(part in {".", ".."} for part in value.split("/"))
    ):
        raise ValueError(f"{field_name} must be a traversal-safe absolute path")
    if value != "/" and value.endswith("/"):
        value = value.rstrip("/")
    return value


SURFACE_TYPES = SurfaceTypeRegistry()
SURFACE_TYPES.register(
    SurfaceTypeDefinition(
        surface_type="browser",
        version=1,
        validate_config=_browser_config_validator,
    )
)
SURFACE_TYPES.register(
    SurfaceTypeDefinition(
        surface_type="mcp_app",
        version=1,
        validate_config=_model_validator(McpAppSurfaceConfig),
    )
)
