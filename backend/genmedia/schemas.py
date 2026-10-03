from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(Strict):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class UserInput(Strict):
    username: str = Field(min_length=1, max_length=80, pattern=r"^[\w.@-]+$")
    password: str = Field(min_length=12, max_length=256)
    role: Literal["user", "admin"] = "user"


class UserUpdate(Strict):
    password: str | None = Field(default=None, min_length=12, max_length=256)
    active: bool | None = None
    role: Literal["user", "admin"] | None = None


class PasswordInput(Strict):
    old_password: str = Field(max_length=256)
    password: str = Field(min_length=12, max_length=256)


class LibraryInput(Strict):
    name: str = Field(min_length=1, max_length=200)
    mode: Literal["managed", "indexed"]
    root_path: str | None = None
    watch_enabled: bool = False


class LibraryUpdate(Strict):
    name: str = Field(min_length=1, max_length=200)
    watch_enabled: bool


class SearchInput(Strict):
    space: Literal["all", "private", "public", "all_private"] = "all"
    uploader_id: UUID | None = None
    query: str = Field(default="", max_length=8192)
    ast: dict | None = None
    library_id: UUID | None = None
    collection_id: UUID | None = None
    trash: bool = False
    sort: Literal["imported_desc", "imported_asc", "created_desc", "rating_desc", "size_desc", "resolution_desc", "filename_asc"] = "imported_desc"
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(default=60, ge=1, le=200)
    facets: bool = False

    @model_validator(mode="after")
    def one_query(self):
        if self.query and self.ast is not None:
            raise ValueError("query 与 ast 不能同时提供")
        return self


class ParseInput(Strict):
    query: str = Field(default="", max_length=8192)
    ast: dict | None = None


class PersonalInput(Strict):
    favorite: bool | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    review: Literal["unreviewed", "keep", "maybe", "reject"] | None = None
    notes: str | None = Field(default=None, max_length=10000)


class BulkInput(Strict):
    asset_ids: list[UUID] = Field(min_length=1, max_length=1000)
    action: Literal["add_tag", "remove_tag", "collection_add", "collection_remove", "favorite", "rating", "review", "trash", "restore", "reparse", "export", "publish", "unpublish", "uploader"]
    value: str | int | bool | None = None


class CollectionInput(Strict):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["static", "smart"] = "static"
    query: str = Field(default="", max_length=8192)
    ast: dict | None = None
    revision: int = Field(default=1, ge=1)


class RuleAction(Strict):
    type: Literal["add_tag", "add_collection"]
    value: str = Field(min_length=1, max_length=200)


class RuleInput(Strict):
    name: str = Field(min_length=1, max_length=200)
    condition: dict
    actions: list[RuleAction] = Field(min_length=1, max_length=20)
    priority: int = Field(default=0, ge=0, le=1000)
    enabled: bool = True


class UserOut(BaseModel):
    id: str
    username: str
    role: str
    active: bool


class SessionOut(BaseModel):
    user: UserOut
    csrf: str


class AssetOut(BaseModel):
    uploader_id: str
    uploader: str
    is_public: bool
    id: str
    library_id: str
    filename: str
    width: int
    height: int
    file_size: int
    imported_at: str
    generator: str
    model: str | None
    prompt: str
    favorite: bool
    rating: int | None
    review: str
    tags: list[str]
    thumbnail: str
    warnings: list[str]
    trashed: bool


class SearchOut(BaseModel):
    items: list[AssetOut]
    next_cursor: str | None
    ast: dict
    facets: dict | None = None
