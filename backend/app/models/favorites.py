"""Versioned favorite library owned by a login user (not a trading account)."""
from sqlalchemy import Column, ForeignKey, Integer, JSON
from app.database import Base


class FavoriteLibrary(Base):
    __tablename__ = 'favorite_libraries'
    user_id = Column(Integer, ForeignKey('users.user_id', ondelete='CASCADE'), primary_key=True)
    revision = Column(Integer, nullable=False, default=0)
    codes = Column(JSON, nullable=False, default=list)
    groups = Column(JSON, nullable=False, default=list)
