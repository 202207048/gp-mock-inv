"""
services/auth_service.py - 인증 비즈니스 로직

서비스(Service)란?
    실제 "비즈니스 로직"을 처리하는 레이어입니다.
    라우터(router)는 요청을 받고 응답을 보내는 역할만,
    서비스는 실제 처리 로직을 담당합니다.
    
    이렇게 분리하는 이유:
    - 코드가 깔끔해짐
    - 나중에 로직 변경 시 서비스 파일만 수정하면 됨
    - 테스트 작성이 쉬워짐

이 파일에서 처리하는 것:
    - 회원가입 (중복 확인, 비밀번호 해싱, 유저/계좌 생성)
    - 로그인 (비밀번호 확인, JWT 토큰 발급)
"""

from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest
from app.utils.security import create_access_token, hash_password, verify_password


def register_user(db: Session, req: RegisterRequest) -> User:
    """
    회원가입 처리 함수.
    
    처리 순서:
        1. 아이디 중복 확인
        2. 이메일 중복 확인
        3. 비밀번호 해싱
        4. 유저 DB에 저장
        5. 기본 모의투자 계좌 자동 생성 (1천만원)
    
    Args:
        db: DB 세션
        req: 회원가입 요청 데이터 (login_id, passwd, user_name, email)
    
    Returns:
        새로 생성된 User 객체
    
    Raises:
        HTTPException 400: 아이디 또는 이메일이 이미 사용 중인 경우
    """
    # 아이디 중복 확인
    # db.query(User).filter(...).first() → 조건에 맞는 첫 번째 유저 조회
    # 없으면 None 반환
    if db.query(User).filter(User.login_id == req.login_id).first():
        raise HTTPException(status_code=400, detail="이미 사용 중인 아이디입니다.")

    # 이메일 중복 확인
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(status_code=400, detail="이미 사용 중인 이메일입니다.")

    # 유저 객체 생성 (아직 DB에 저장 안 됨)
    user = User(
        login_id=req.login_id,
        passwd=hash_password(req.passwd),  # 비밀번호는 반드시 해싱해서 저장!
        user_name=req.user_name,
        email=req.email,
    )
    db.add(user)    # DB에 추가 예약
    db.flush()      # DB에 실제 전송 (아직 commit 전) → user_id를 받아오기 위해 필요

    # 회원가입 시 기본 모의투자 계좌 자동 생성
    # 시작 자산: 1,000만원 (10,000,000원)
    account = Account(
        user_id=user.user_id,           # 방금 생성된 유저의 ID
        account_name="모의투자 기본계좌",
        balance=10_000_000,             # 총 자산 1천만원
        withdrawable_cash=10_000_000,   # 매수 가능 현금 1천만원
    )
    db.add(account)
    db.commit()         # 유저 + 계좌 동시에 DB에 최종 저장
    db.refresh(user)    # DB에서 최신 데이터 다시 불러오기 (user_id 등 자동생성값 반영)
    return user


def login_user(db: Session, req: LoginRequest) -> str:
    """
    로그인 처리 함수.
    
    처리 순서:
        1. 아이디로 유저 조회
        2. 비밀번호 검증
        3. 마지막 로그인 시간 업데이트
        4. JWT 토큰 생성 및 반환
    
    Args:
        db: DB 세션
        req: 로그인 요청 데이터 (login_id, passwd)
    
    Returns:
        JWT 액세스 토큰 문자열
    
    Raises:
        HTTPException 401: 아이디 또는 비밀번호가 틀린 경우
    """
    # 아이디로 유저 조회
    user = db.query(User).filter(User.login_id == req.login_id).first()

    # 유저가 없거나 비밀번호가 틀린 경우
    # 보안상 "아이디가 없습니다" / "비밀번호가 틀렸습니다"를 구분하지 않고
    # 동일한 메시지로 응답 (어떤 게 틀렸는지 알려주면 해킹에 활용될 수 있음)
    if not user or not verify_password(req.passwd, user.passwd):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="아이디 또는 비밀번호가 올바르지 않습니다.",
        )

    # 마지막 로그인 시간 기록 (보안 모니터링용)
    user.last_login = datetime.utcnow()
    db.commit()

    # JWT 토큰 생성
    # "sub" 키에 user_id를 문자열로 저장 (JWT 표준 관례)
    token = create_access_token(data={"sub": str(user.user_id)})
    return token
