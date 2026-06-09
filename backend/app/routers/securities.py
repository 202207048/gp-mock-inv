"""
routers/securities.py - 종목 관련 API 엔드포인트

제공하는 API:
    GET /securities                     → 종목 목록/검색
    GET /securities/{code}              → 종목 상세 정보
    GET /securities/{code}/price        → 현재가 조회 (KIS API)
    GET /securities/{code}/chart        → 차트 데이터 조회

종목 목록/현재가는 한국투자증권 API(KIS)와 연동됩니다.
KIS API 키가 없으면 현재가는 0으로 반환됩니다.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.security import ItemMaster
from app.models.user import User
from app.schemas.security import SecurityResponse
from app.services.kis_service import get_current_price, get_stock_chart
from app.utils.deps import get_current_user

# prefix는 main.py에서 /api/stocks 로 지정
# 최종 경로 예시: /api/stocks, /api/stocks/005930/price
router = APIRouter(tags=["주식 시세"])


@router.get("", response_model=list[SecurityResponse], summary="종목 목록 조회")
def get_securities(
    # Query(): URL 쿼리 파라미터 (?market_type=국내주식&search=삼성 형태)
    market_type: str | None = Query(None, description="국내주식, ELW, 선물옵션"),
    search: str | None = Query(None, description="종목명 또는 코드 검색"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),  # 로그인 확인만 하고 유저 정보는 안 씀 (_로 표시)
):
    """
    종목 목록을 조회합니다.
    
    검색 예시:
        GET /securities?search=삼성      → "삼성"이 포함된 모든 종목
        GET /securities?market_type=국내주식 → 국내주식만
        GET /securities?search=005930   → 종목 코드로 검색
    
    주의: security 테이블에 데이터가 없으면 빈 목록이 반환됩니다.
    DB팀과 협의해서 초기 종목 데이터를 삽입해야 합니다.
    """
    query = db.query(ItemMaster)

    if market_type:
        query = query.filter(ItemMaster.market_type == market_type)

    if search:
        query = query.filter(
            ItemMaster.name.ilike(f"%{search}%") |
            ItemMaster.symbol_code.ilike(f"%{search}%")
        )

    return query.limit(100).all()  # 최대 100개까지


@router.get("/{symbol_code}", response_model=SecurityResponse, summary="종목 상세 조회")
def get_security(
    symbol_code: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    특정 종목의 기본 정보를 반환합니다. (종목명, 시장구분, 업종코드)
    실시간 가격은 /price 엔드포인트를 사용하세요.
    """
    security = db.query(ItemMaster).filter(ItemMaster.symbol_code == symbol_code).first()
    if not security:
        raise HTTPException(status_code=404, detail="종목을 찾을 수 없습니다.")
    return security


@router.get("/{symbol_code}/price", summary="현재가 조회 (KIS API)")
async def get_price(
    symbol_code: str,
    _: User = Depends(get_current_user),
):
    """
    한국투자증권 API를 통해 실시간 현재가를 조회합니다.
    
    async def: 비동기 함수 (KIS API 응답을 기다리는 동안 다른 요청 처리 가능)
    
    응답 예시:
        {
            "symbol_code": "005930",
            "current_price": 75000,
            "change_rate": 1.5,
            "high": 76000,
            "low": 74500,
            "volume": 15000000
        }
    """
    return await get_current_price(symbol_code)


@router.get("/{symbol_code}/chart", summary="차트 데이터 조회")
async def get_chart(
    symbol_code: str,
    # Query로 period 파라미터 받기 (기본값 "D" = 일봉)
    period: str = Query("D", description="D(일봉), W(주봉), M(월봉)"),
    _: User = Depends(get_current_user),
):
    """
    차트 데이터(캔들스틱)를 반환합니다.
    
    프론트엔드의 차트 라이브러리(예: Recharts, Chart.js)에 전달할 형태로 반환합니다.
    
    응답 예시:
        [
            {"date": "20240115", "open": 74000, "high": 76000, "low": 73500, "close": 75000, "volume": 15000000},
            ...
        ]
    """
    return await get_stock_chart(symbol_code, period)
