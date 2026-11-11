import re
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

METHODS = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]
FORBIDDEN_HEADERS = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class RouterError(Exception):
    def __init__(self, status_code, code, public_message):
        self.status_code, self.code, self.public_message = status_code, code, public_message


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthSpec(Model):
    mode: Literal["header", "query"] = "header"
    token: str | None = Field(None, min_length=1, max_length=4096)


class FieldSpec(Model):
    id: str | None = None
    name: str = Field(min_length=1, max_length=200)
    value: str | None = None
    secret: bool = False
    configured: bool = False
    value_action: Literal["keep", "replace", "remove"] = "replace"


class BodySpec(Model):
    mode: Literal["none", "passthrough", "text", "json"] = "none"
    template: Any = None
    secret: bool = False
    configured: bool = False
    value_action: Literal["keep", "replace", "remove"] = "replace"


class TargetSpec(Model):
    method: METHODS = "POST"
    url: str = Field(max_length=8192)
    query: list[FieldSpec] = Field(default_factory=list, max_length=100)
    headers: list[FieldSpec] = Field(default_factory=list, max_length=100)
    body: BodySpec = Field(default_factory=BodySpec)

    @model_validator(mode="after")
    def validate_target(self):
        from .templating import validate_templates

        try:
            parts = urlsplit(self.url)
            port = parts.port
        except ValueError:
            raise ValueError("Invalid target URL") from None
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
            or "{{" in parts.netloc
            or "}}" in parts.netloc
            or any(ord(c) < 33 for c in self.url)
        ):
            raise ValueError(
                "Target must have a fixed HTTP(S) authority, without credentials or fragment"
            )
        if port is not None and port < 1:
            raise ValueError("Invalid port")
        # URL query is fixed; use target.query for templates.
        if "{{" in parts.query or "}}" in parts.query:
            raise ValueError("Use target.query for query templates")
        names = set()
        for header in self.headers:
            name = header.name.lower()
            if (
                not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", header.name)
                or name in FORBIDDEN_HEADERS
                or name in names
            ):
                raise ValueError("Invalid, duplicate or reserved header")
            names.add(name)
            if header.value and ("\r" in header.value or "\n" in header.value):
                raise ValueError("Invalid header value")
        if self.method in {"GET", "HEAD"} and self.body.mode != "none":
            raise ValueError("GET and HEAD targets cannot have a body")
        if (
            self.body.mode == "text"
            and self.body.value_action != "keep"
            and not isinstance(self.body.template, str)
        ):
            raise ValueError("Text body requires a string")
        validate_templates(parts.path)
        for item in self.query + self.headers:
            if item.value is not None:
                validate_templates(item.value)
        if self.body.mode in {"text", "json"} and self.body.value_action != "keep":
            validate_templates(self.body.template)
        return self


class HookSpec(Model):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=2000)
    enabled: bool = False
    input_method: METHODS = "POST"
    input_path: str
    auth: AuthSpec = Field(default_factory=AuthSpec)
    target: TargetSpec
    timeout_seconds: int = Field(60, ge=1, le=900)
    capture_bodies: bool = False
    secret_refs: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_path(self):
        from .routing import compile_route

        compile_route(self.input_method, self.input_path)
        return self


class UserCreate(Model):
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{3,64}$")
    password: str = Field(min_length=8, max_length=1024)
    role: Literal["admin", "user"] = "user"
    active: bool = True


class UserPatch(Model):
    role: Literal["admin", "user"] | None = None
    active: bool | None = None


@dataclass
class Principal:
    id: str
    username: str
    role: str


@dataclass
class IncomingRequest:
    request_id: str
    method: str
    raw_path: bytes
    query: list[tuple[str, str]]
    headers: list[tuple[str, str]]
    body: bytes
    path: dict[str, str] = field(default_factory=dict)


@dataclass
class TemplateContext:
    path: dict
    query: dict
    headers: dict
    body: Any
    secret_refs: set[str] = field(default_factory=set)


@dataclass
class RenderedRequest:
    method: str
    url: str
    headers: list[tuple[str, str]]
    body: bytes
    secret_fields: set[str]


@dataclass
class ProxyReply:
    status_code: int
    headers: dict[str, str]
    body: bytes
