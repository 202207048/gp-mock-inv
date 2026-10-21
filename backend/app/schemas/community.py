from datetime import datetime

import base64
import binascii
from pydantic import BaseModel, Field, field_validator


class PollCreate(BaseModel):
    question: str = Field(min_length=1, max_length=200)
    options: list[str] = Field(min_length=2, max_length=4)

    @field_validator("question")
    @classmethod
    def question_text(cls, value):
        if not value.strip():
            raise ValueError("투표 질문을 입력해 주세요.")
        return value.strip()

    @field_validator("options")
    @classmethod
    def option_text(cls, values):
        values = [value.strip() for value in values]
        if any(not value or len(value) > 100 for value in values) or len(set(values)) != len(values):
            raise ValueError("서로 다른 선택지를 2~4개 입력해 주세요. (각 100자 이내)")
        return values


class PostCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=10000)
    symbol_code: str | None = Field(default=None, max_length=20)
    images: list[str] = Field(default_factory=list, max_length=4)
    poll: PollCreate | None = None
    trade_id: int | None = Field(default=None, gt=0)
    quote_post_id: int | None = Field(default=None, gt=0)

    @field_validator("title", "content")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("제목과 내용을 입력해 주세요.")
        return value.strip()

    @field_validator("images")
    @classmethod
    def valid_images(cls, values):
        for value in values:
            if len(value) > 1400000:
                raise ValueError("사진은 장당 1MB 이하여야 합니다.")
            header, _, payload = value.partition(",")
            if header not in ("data:image/jpeg;base64", "data:image/png;base64", "data:image/webp;base64"):
                raise ValueError("JPG, PNG, WEBP 사진만 첨부할 수 있습니다.")
            try:
                raw = base64.b64decode(payload, validate=True)
            except (ValueError, binascii.Error):
                raise ValueError("올바른 사진 파일이 아닙니다.")
            valid = ((header == "data:image/jpeg;base64" and raw.startswith(b"\xff\xd8\xff"))
                     or (header == "data:image/png;base64" and raw.startswith(b"\x89PNG\r\n\x1a\n"))
                     or (header == "data:image/webp;base64" and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"))
            if not valid or len(raw) > 1024 * 1024:
                raise ValueError("올바른 1MB 이하 사진을 첨부해 주세요.")
        return values


class VoteRequest(BaseModel):
    option_index: int = Field(ge=0, le=3)


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
