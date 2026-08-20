import secrets

from app.database import User
from app.database._base import DatabaseModel

from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlmodel import Column, Field, ForeignKey, Integer, Relationship


class BanchoPyAPIKeys(DatabaseModel, AsyncAttrs, table=True):
    """Database table for bancho.py API keys."""

    __tablename__ = "api_keys"

    id: int = Field(default=None, primary_key=True)
    name: str = Field(max_length=100, index=True)
    key: str = Field(default_factory=secrets.token_hex, index=True)
    owner_id: int = Field(sa_column=Column(Integer, ForeignKey("lazer_users.id"), index=True))

    owner: "User" = Relationship()
