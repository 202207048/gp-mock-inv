"""
routers/auth.py - 인증 관련 API 엔드포인트

라우터(Router)란?
    URL 경로와 함수를 연결해주는 역할을 합니다.
    예: POST /auth/login 요청이 오면 login() 함수를 실행

이 파일에서 제공하는 API:
    POST /auth/register → 회원가입
    POST /auth/login    → 로그인 (JWT 토큰 발급)
    GET  /auth/me       → 내 정보 조회 (로그인 필요)
    PUT  /auth/survey   → 투자 성향 저장 (로그인 필요)

prefix="/auth": 모든 경로 앞에 /auth 가 자동으로 붙음
tags=["인증"]: /docs 페이지에서 이 API들이 "인증" 그룹으로 묶임
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.schemas.auth import (
    InvestmentSurveyRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.services.auth_service import login_user, register_user
from app.utils.deps import get_current_user

# APIRouter 객체 생성
# prefix: 이 라우터의 모든 경로 앞에 붙는 공통 접두사
# prefix는 main.py에서 /api/users 로 지정하므로 여기선 비워둠
# 최종 경로 예시: /api/users/register, /api/users/login
router = APIRouter(tags=["사용자/인증"])


@router.post("/register", response_model=UserResponse, summary="회원가입")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    """
    새 유저를 등록합니다.
    
    - 아이디/이메일 중복 확인
    - 비밀번호 bcrypt 해싱 저장
    - 기본 모의투자 계좌 자동 생성 (1천만원)
    
    요청 예시:
        POST /auth/register
        {
            "login_id": "testuser",
            "passwd": "password123",
            "user_name": "홍길동",
            "email": "hong@test.com"
        }
    """
    # Depends(get_db): DB 세션을 자동으로 주입받음 (database.py의 get_db 함수)
    user = register_user(db, req)
    return user


@router.post("/login", response_model=TokenResponse, summary="로그인")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    """
    로그인 후 JWT 토큰을 발급합니다.
    
    프론트엔드는 이 토큰을 저장해두고 이후 모든 API 요청 헤더에 포함:
        Authorization: Bearer eyJhbGci...
    
    요청 예시:
        POST /auth/login
        {"login_id": "testuser", "passwd": "password123"}
    
    응답 예시:
        {"access_token": "eyJhbGci...", "token_type": "bearer"}
    """
    token = login_user(db, req)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse, summary="내 정보 조회")
def get_me(current_user: User = Depends(get_current_user)):
    """
    현재 로그인한 유저의 정보를 반환합니다.
    
    Depends(get_current_user): 
        요청 헤더의 JWT 토큰을 자동으로 검증하고 유저 정보를 가져옵니다.
        토큰이 없거나 유효하지 않으면 401 오류 반환.
    
    응답 예시:
        {
            "user_id": 1,
            "login_id": "testuser",
            "user_name": "홍길동",
            "email": "hong@test.com",
            "investment_style": null
        }
    """
    # current_user는 get_current_user 의존성이 자동으로 채워줌
    return current_user


@router.put("/survey", response_model=UserResponse, summary="투자 성향 저장")
def save_investment_survey(
    req: InvestmentSurveyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    AI가 분석한 투자 성향을 저장합니다.
    
    투자 성향 설문이 완료된 후 결과를 저장하는 API입니다.
    
    요청 예시:
        PUT /auth/survey
        {"investment_style": "공격형"}
    """
    # 로그인한 유저의 investment_style 업데이트
    current_user.investment_style = req.investment_style
    db.commit()
    db.refresh(current_user)
    return current_user
