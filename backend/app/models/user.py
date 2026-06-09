"""
models/user.py - 사용자(Users) 테이블 모델

DB의 users 테이블 구조를 파이썬 클래스로 정의합니다.
SQLAlchemy ORM을 사용하므로 이 클래스 하나로
테이블 생성, 데이터 조회/저장/수정/삭제가 모두 가능합니다.

실제 DB 테이블:
    CREATE TABLE users (
        user_id SERIAL PRIMARY KEY,
        login_id VARCHAR(50) UNIQUE NOT NULL,
        ...
    )
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.orm import relationship

from app.database import Base


class User(Base):
    """
    사용자 테이블 모델.
    
    DB팀이 설계한 users 테이블을 그대로 파이썬 클래스로 표현한 것입니다.
    """

    # 이 클래스가 연결될 DB 테이블 이름
    __tablename__ = "users"

    # 사용자 고유 번호 (자동 증가: 1, 2, 3, ...)
    # primary_key=True: 이 컬럼이 테이블의 기본키(PK)
    # index=True: 검색 속도를 높이는 인덱스 생성
    user_id = Column(Integer, primary_key=True, index=True)

    # 로그인 아이디 (중복 불가, 비어있을 수 없음)
    # unique=True: 같은 아이디 두 번 등록 불가
    login_id = Column(String(50), unique=True, nullable=False, index=True)

    # 비밀번호 (bcrypt로 해싱된 값이 저장됨, 절대 원문 저장 X)
    # 예: "1234" → "$2b$12$abc..." 형태로 저장
    passwd = Column(String(255), nullable=False)

    # 사용자 이름 (닉네임)
    user_name = Column(String(50), nullable=False)

    # 이메일 (비밀번호 찾기에 사용)
    email = Column(String(100), unique=True, nullable=False)

    # 가입일자 (자동으로 현재 시간 저장)
    created = Column(DateTime, default=datetime.utcnow)

    # AI가 분석한 투자 성향 (예: "공격형", "안정형", "중립형")
    # nullable=True: 처음엔 비어있고 투자성향 설문 완료 후 채워짐
    investment_style = Column(String(50), nullable=True)

    # 마지막 로그인 시간 (보안 관리 목적)
    last_login = Column(DateTime, nullable=True)

    # -----------------------------------------------
    # 관계(Relationship) 설정
    # -----------------------------------------------
    # 다른 테이블과의 연결을 설정합니다
    # 예: user.accounts → 이 유저의 계좌 목록을 바로 가져올 수 있음

    # 이 유저가 가진 계좌 목록 (1:N 관계 - 유저 1명이 계좌 여러개)
    accounts = relationship("Account", back_populates="user")

    # 이 유저가 작성한 게시글 목록
    posts = relationship("Post", back_populates="author")

    # 나를 팔로우하는 사람들 목록 (팔로워)
    followers = relationship(
        "Follow",
        foreign_keys="Follow.following_id",
        back_populates="following"
    )

    # 내가 팔로우하는 사람들 목록 (팔로잉)
    following = relationship(
        "Follow",
        foreign_keys="Follow.follower_id",
        back_populates="follower"
    )

    survey_responses = relationship("UserSurveyResponse", back_populates="user")
    ai_advices = relationship("AiPropensityAdvice", back_populates="user")
