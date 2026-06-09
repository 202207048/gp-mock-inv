"""
schemas/auth.py - 인증 관련 요청/응답 데이터 형식 정의

Pydantic 스키마(Schema)란?
    API가 받는 데이터(요청)와 보내는 데이터(응답)의 형식을 정의합니다.
    자동으로 데이터 유효성 검사를 해줍니다.
    
    예: 이메일 형식이 잘못됐으면 자동으로 422 오류 반환

모델(Model) vs 스키마(Schema) 차이:
    Model  (models/)  → DB 테이블 구조 정의 (SQLAlchemy)
    Schema (schemas/) → API 요청/응답 형식 정의 (Pydantic)
"""

from pydantic import BaseModel, EmailStr


class RegisterRequest(BaseModel):
    """
    회원가입 요청 데이터 형식.
    
    프론트엔드에서 POST /auth/register 로 보내는 데이터:
    {
        "login_id": "myid123",
        "passwd": "mypassword",
        "user_name": "홍길동",
        "email": "hong@example.com"
    }
    """
    login_id: str       # 로그인 아이디
    passwd: str         # 비밀번호 (원문, 서버에서 해싱)
    user_name: str      # 사용자 이름
    email: EmailStr     # 이메일 (EmailStr: 형식 자동 검증)


class LoginRequest(BaseModel):
    """
    로그인 요청 데이터 형식.
    
    {
        "login_id": "myid123",
        "passwd": "mypassword"
    }
    """
    login_id: str
    passwd: str


class TokenResponse(BaseModel):
    """
    로그인 성공 시 응답 데이터 형식.
    
    프론트엔드가 받는 데이터:
    {
        "access_token": "eyJhbGci...",
        "token_type": "bearer"
    }
    프론트엔드는 access_token을 저장해두고 이후 API 요청 헤더에 포함시킵니다.
    """
    access_token: str
    token_type: str = "bearer"  # 항상 "bearer" (기본값)


class UserResponse(BaseModel):
    """
    유저 정보 응답 데이터 형식.
    
    민감한 정보(passwd)는 제외하고 응답합니다.
    {
        "user_id": 1,
        "login_id": "myid123",
        "user_name": "홍길동",
        "email": "hong@example.com",
        "investment_style": "공격형"
    }
    """
    user_id: int
    login_id: str
    user_name: str
    email: str
    investment_style: str | None = None  # 투자성향 미설정 시 null

    class Config:
        # from_attributes=True: SQLAlchemy 모델 객체를 Pydantic으로 변환 허용
        # 예: UserResponse.model_validate(user_db_object)
        from_attributes = True


class InvestmentSurveyRequest(BaseModel):
    """
    투자 성향 설문 결과 저장 요청.
    
    AI팀이 분석한 투자 성향을 저장합니다.
    {
        "investment_style": "공격형"
    }
    """
    investment_style: str
