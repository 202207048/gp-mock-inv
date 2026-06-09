"""
utils/security.py - 보안 관련 유틸리티 함수

비밀번호 해싱과 JWT 토큰 관련 함수들을 모아둔 파일입니다.

비밀번호 해싱이란?
    비밀번호를 그대로 DB에 저장하면 위험합니다.
    해킹 당했을 때 모든 비밀번호가 유출되기 때문입니다.
    bcrypt로 해싱하면 "1234" → "$2b$12$abc..." 같은 알아볼 수 없는 값으로 변환됩니다.
    원래 비밀번호로 복원할 수 없고, 비교만 가능합니다.

JWT(JSON Web Token)란?
    로그인 성공 시 서버가 발급하는 "디지털 신분증"입니다.
    프론트엔드가 이 토큰을 저장해두고, API 요청마다 헤더에 담아 보냅니다.
    서버는 토큰을 검증해서 누가 요청했는지 확인합니다.
    
    토큰 구조: header.payload.signature
    payload 안에 user_id 등의 정보가 들어있습니다.
"""

from datetime import datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings


# bcrypt 암호화 설정
# CryptContext: 비밀번호 해싱/검증을 편리하게 해주는 객체
# schemes=["bcrypt"]: bcrypt 알고리즘 사용 (업계 표준)
# deprecated="auto": 오래된 해싱 방식은 자동으로 경고
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    """
    평문 비밀번호를 bcrypt로 해싱합니다.
    
    회원가입 시 사용합니다.
    
    Args:
        plain_password: 사용자가 입력한 원본 비밀번호 (예: "mypassword123")
    
    Returns:
        해싱된 비밀번호 문자열 (예: "$2b$12$abc...xyz")
        이 값을 DB에 저장합니다.
    """
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    입력한 비밀번호와 DB에 저장된 해시를 비교합니다.
    
    로그인 시 사용합니다.
    
    Args:
        plain_password: 로그인 시 입력한 비밀번호 (예: "mypassword123")
        hashed_password: DB에 저장된 해시값 (예: "$2b$12$abc...xyz")
    
    Returns:
        True  → 비밀번호 일치 (로그인 성공)
        False → 비밀번호 불일치 (로그인 실패)
    """
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict) -> str:
    """
    JWT 액세스 토큰을 생성합니다.
    
    로그인 성공 후 이 토큰을 프론트엔드에 전달합니다.
    프론트엔드는 이후 모든 API 요청에 이 토큰을 포함시켜야 합니다.
    
    Args:
        data: 토큰에 담을 데이터 (예: {"sub": "1"} → user_id가 1인 유저)
    
    Returns:
        JWT 토큰 문자열 (예: "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.xxx")
    
    토큰에는 만료 시간(exp)도 자동으로 포함됩니다.
    """
    to_encode = data.copy()

    # 만료 시간 계산 (현재 시간 + 설정된 분)
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})

    # SECRET_KEY로 토큰 서명 → 서버만 알고 있는 키로 위조 방지
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """
    JWT 토큰을 디코딩해서 안의 정보를 꺼냅니다.
    
    API 요청 시 토큰이 유효한지 검증하는 데 사용합니다.
    
    Args:
        token: 프론트엔드가 보낸 JWT 토큰 문자열
    
    Returns:
        유효한 토큰 → 토큰 안의 데이터 dict (예: {"sub": "1", "exp": ...})
        유효하지 않은 토큰 → None (만료됐거나 위조된 토큰)
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        # 토큰이 만료됐거나, 형식이 잘못됐거나, 서명이 위조된 경우
        return None
