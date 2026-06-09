"""
utils/deps.py - 의존성 주입(Dependency Injection) 함수

FastAPI의 핵심 기능인 의존성 주입을 활용합니다.

의존성 주입이란?
    API 함수가 실행되기 전에 자동으로 필요한 것들을 준비해주는 것입니다.
    예: 로그인이 필요한 API에 get_current_user를 달아두면,
        API 실행 전 자동으로 토큰 확인 → 유저 정보 조회 → API에 전달

사용 예시:
    @router.get("/my-info")
    def get_my_info(current_user: User = Depends(get_current_user)):
        # current_user는 자동으로 채워짐 (로그인한 유저 객체)
        return current_user.user_name
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.utils.security import decode_access_token


# Bearer 토큰 방식 인증 설정
# HTTP 요청의 Authorization 헤더에서 "Bearer 토큰값" 형태를 파싱해줌
# 예: Authorization: Bearer eyJhbGciOiJIUzI1NiJ9...
bearer_scheme = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    현재 로그인한 유저를 가져오는 의존성 함수.
    
    이 함수를 API에 Depends로 달면:
        1. 요청 헤더에서 JWT 토큰을 자동으로 추출
        2. 토큰의 유효성 검증
        3. 토큰에서 user_id 추출
        4. DB에서 해당 유저 조회
        5. API 함수에 User 객체 전달
    
    토큰이 없거나 유효하지 않으면 401 Unauthorized 오류 반환
    (API 실행 자체가 안 됨 → 로그인하지 않은 사람 접근 차단)
    
    Args:
        credentials: HTTPBearer가 자동으로 헤더에서 추출한 토큰 정보
        db: DB 세션 (get_db 의존성으로 자동 주입)
    
    Returns:
        로그인한 유저의 User 모델 객체
    
    Raises:
        HTTPException 401: 토큰이 없거나 유효하지 않은 경우
    """
    # credentials.credentials = 실제 토큰 문자열 (Bearer 다음 부분)
    token = credentials.credentials

    # 토큰 디코딩 시도
    payload = decode_access_token(token)

    if payload is None:
        # 토큰이 만료됐거나 위조된 경우
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않거나 만료된 토큰입니다.",
        )

    # 토큰에서 user_id 추출 (토큰 생성 시 "sub" 키에 user_id를 넣었음)
    user_id: int = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="토큰에 사용자 정보가 없습니다.",
        )

    # DB에서 실제 유저 조회 (탈퇴한 유저 토큰 방지)
    user = db.query(User).filter(User.user_id == int(user_id)).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="사용자를 찾을 수 없습니다.",
        )

    return user
