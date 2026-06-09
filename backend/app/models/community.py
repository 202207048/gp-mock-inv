"""
models/community.py - 커뮤니티 관련 테이블 모델

커뮤니티 기능에 필요한 3개의 테이블을 정의합니다:
    1. Post      - 게시글
    2. Comment   - 댓글
    3. PostLike  - 게시글 좋아요

테이블 관계:
    User (1) → Post (N)      : 한 유저가 여러 게시글 작성 가능
    Post (1) → Comment (N)   : 한 게시글에 여러 댓글 가능
    Post (1) → PostLike (N)  : 한 게시글에 여러 좋아요 가능
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class Post(Base):
    """
    게시글 테이블 모델.
    
    종목별 게시판 기능을 위해 symbol_code를 선택적으로 입력 가능합니다.
    symbol_code가 있으면 해당 종목 게시판, 없으면 자유게시판으로 사용합니다.
    """

    __tablename__ = "posts"

    # 게시글 고유 번호
    post_id = Column(Integer, primary_key=True, index=True)

    # 작성자 user_id
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)

    # 종목별 게시판 연결 (선택사항)
    # 예: "005930" 이면 삼성전자 종목 게시판 게시글
    # None이면 일반 자유게시판 게시글
    symbol_code = Column(String(20), nullable=True)

    # 게시글 제목
    title = Column(String(200), nullable=False)

    # 게시글 내용 (Text: 길이 제한 없이 긴 글 저장 가능)
    content = Column(Text, nullable=False)

    # 작성일시
    created_at = Column(DateTime, default=datetime.utcnow)

    # 수정일시 (수정할 때마다 자동 업데이트)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow  # UPDATE 쿼리 실행 시 자동으로 현재 시간 저장
    )

    # 관계 설정
    author = relationship("User", back_populates="posts")

    # cascade="all, delete-orphan": 게시글 삭제 시 댓글/좋아요도 자동 삭제
    comments = relationship("Comment", back_populates="post", cascade="all, delete-orphan")
    likes = relationship("PostLike", back_populates="post", cascade="all, delete-orphan")


class Comment(Base):
    """
    댓글 테이블 모델.
    
    어떤 게시글(post_id)에 어떤 유저(user_id)가 댓글을 달았는지 기록합니다.
    """

    __tablename__ = "comments"

    # 댓글 고유 번호
    comment_id = Column(Integer, primary_key=True, index=True)

    # 어떤 게시글의 댓글인지
    post_id = Column(Integer, ForeignKey("posts.post_id"), nullable=False)

    # 댓글 작성자
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)

    # 댓글 내용
    content = Column(Text, nullable=False)

    # 댓글 작성일시
    created_at = Column(DateTime, default=datetime.utcnow)

    # 관계 설정
    post = relationship("Post", back_populates="comments")
    author = relationship("User")


class PostLike(Base):
    """
    게시글 좋아요 테이블 모델.
    
    같은 유저가 같은 게시글에 좋아요를 두 번 누를 수 없도록
    post_id + user_id 조합으로 중복 체크를 합니다.
    좋아요를 다시 누르면 좋아요가 취소됩니다 (토글 방식).
    """

    __tablename__ = "post_likes"

    # 좋아요 고유 번호
    like_id = Column(Integer, primary_key=True, index=True)

    # 어떤 게시글에 좋아요를 눌렀는지
    post_id = Column(Integer, ForeignKey("posts.post_id"), nullable=False)

    # 누가 좋아요를 눌렀는지
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)

    # 관계 설정
    post = relationship("Post", back_populates="likes")
    user = relationship("User")
