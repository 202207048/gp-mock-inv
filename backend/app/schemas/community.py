from datetime import datetime

from pydantic import BaseModel


class PostCreateRequest(BaseModel):
    title: str
    content: str
    symbol_code: str | None = None


class PostResponse(BaseModel):
    post_id: int
    user_id: int
    author_name: str
    symbol_code: str | None
    title: str
    content: str
    created_at: datetime
    like_count: int = 0
    comment_count: int = 0

    class Config:
        from_attributes = True


class CommentCreateRequest(BaseModel):
    content: str


class CommentResponse(BaseModel):
    comment_id: int
    user_id: int
    author_name: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True
