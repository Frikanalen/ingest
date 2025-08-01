# generated by datamodel-codegen:
#   filename:  hook-request.schema.json

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, RootModel


class Header(RootModel[Optional[dict[str, list[str]]]]):
    root: dict[str, list[str]] | None = None


class MetaData(RootModel[Optional[dict[str, str]]]):
    root: dict[str, str] | None = None


class FileInfo(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    id: str | None = Field(None, alias="ID")
    size: int | None = Field(None, alias="Size")
    size_is_deferred: bool | None = Field(None, alias="SizeIsDeferred")
    offset: int | None = Field(None, alias="Offset")
    meta_data: MetaData | None = Field(None, alias="MetaData")
    is_partial: bool | None = Field(None, alias="IsPartial")
    is_final: bool | None = Field(None, alias="IsFinal")
    partial_uploads: list[str] | None = Field(None, alias="PartialUploads")
    storage: dict[str, str] | None = Field(None, alias="Storage")


class HTTPRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    method: str | None = Field(None, alias="Method")
    uri: str | None = Field(None, alias="URI")
    remote_addr: str | None = Field(None, alias="RemoteAddr")
    header: Header | None = Field(None, alias="Header")


class HookEvent(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    upload: FileInfo | None = Field(None, alias="Upload")
    http_request: HTTPRequest | None = Field(None, alias="HTTPRequest")


class HookRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: str | None = Field(None, alias="Type")
    event: HookEvent | None = Field(None, alias="Event")


class Model(RootModel[Optional[HookRequest]]):
    root: HookRequest | None = None
