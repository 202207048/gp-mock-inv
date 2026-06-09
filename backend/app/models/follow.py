"""
models/follow.py - 팔로우 테이블 모델

유저 간 팔로우/팔로워 관계를 저장합니다.
SNS처럼 다른 투자자를 팔로우하는 기능에 사용됩니다.

예시:
    A가 B를 팔로우하면:
        follower_id = A의 user_id  (팔로우를 하는 사람)
        following_id = B의 user_id (팔로우를 받는 사람)
    
    "A가 B를 팔로우한다" = "B 입장에서 A는 팔로워"

users 테이블 자기 참조:
    follows 테이블의 follower_id와 following_id 둘 다 users 테이블을 참조합니다.
    이를 "자기 참조 관계"라고 합니다.
"""

from sqlalchemy import Column, ForeignKey, Integer
from sqlalchemy.orm import relationship

from app.database import Base


class Follow(Base):
    """팔로우 관계 테이블 모델."""

    __tablename__ = "follows"

    # 팔로우 관계 고유 번호
    follow_id = Column(Integer, primary_key=True, index=True)

    # 팔로우를 누른 사람 (나)
    follower_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)

    # 팔로우를 받는 사람 (상대방)
    following_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)

    # 관계 설정
    # foreign_keys를 명시해야 함 - 같은 테이블(users)을 두 번 참조하므로
    # 어떤 FK가 어떤 관계인지 SQLAlchemy에게 알려줘야 함

    # "팔로우를 누른 사람" → User 객체
    follower = relationship(
        "User",
        foreign_keys=[follower_id],
        back_populates="following"  # User.following 에서 접근 가능
    )

    # "팔로우를 받는 사람" → User 객체
    following = relationship(
        "User",
        foreign_keys=[following_id],
        back_populates="followers"  # User.followers 에서 접근 가능
    )
