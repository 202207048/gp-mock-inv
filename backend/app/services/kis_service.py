"""
services/kis_service.py - 한국투자증권 오픈API 연동 서비스

한국투자증권 오픈API를 통해 실시간 주가 정보를 가져옵니다.

사전 준비:
    1. https://apiportal.koreainvestment.com 에서 회원가입
    2. 앱 등록 후 APP KEY, APP SECRET 발급
    3. .env 파일에 KIS_APP_KEY, KIS_APP_SECRET 입력

토큰 발급 제한:
    - 한국투자증권 모의투자 API는 하루에 발급 가능한 토큰 수에 제한이 있음
    - 매번 새로 발급받으면 금방 한도 초과 → API 차단
    - 해결: 토큰을 메모리에 저장해두고 23시간 동안 재사용
    - 23시간이 지나면 자동으로 새 토큰 발급
"""

from datetime import datetime, timedelta

import httpx

from app.config import settings

# 한국투자증권 API 기본 주소
# 모의투자: https://openapivts.koreainvestment.com:29443  (포트 29443)
# 실전투자: https://openapi.koreainvestment.com:9443      (포트 9443)
KIS_BASE_URL = "https://openapivts.koreainvestment.com:29443"

# 토큰 캐시 (딕셔너리에 저장해서 재사용)
# 구조:
#   _token_cache["token"]      → 발급받은 토큰 문자열
#   _token_cache["expires_at"] → 만료 시각 (datetime 객체)
_token_cache: dict = {}


async def get_kis_token() -> str:
    """
    KIS API 접근 토큰을 반환합니다.

    토큰이 유효하면 (23시간 이내) 저장된 토큰을 재사용합니다.
    만료됐거나 없으면 새로 발급받아 저장합니다.

    Returns:
        KIS API 접근 토큰 문자열
    """
    # 현재 시각
    now = datetime.utcnow()

    # 토큰이 있고, 아직 만료되지 않았으면 → 재사용
    # _token_cache.get("token"): 토큰이 저장되어 있는지 확인
    # _token_cache.get("expires_at"): 만료 시각이 저장되어 있는지 확인
    # now < _token_cache["expires_at"]: 현재 시각이 만료 시각보다 이전인지 확인
    if (
        _token_cache.get("token") and
        _token_cache.get("expires_at") and
        now < _token_cache["expires_at"]
    ):
        return _token_cache["token"]

    # 토큰이 없거나 만료됐으면 → 새로 발급받기
    # verify=False: KIS 모의투자 서버의 SSL 인증서 검증 비활성화 (인증서 호스트명 불일치 문제 우회)
    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.post(
            f"{KIS_BASE_URL}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": settings.KIS_APP_KEY,
                "appsecret": settings.KIS_APP_SECRET,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        # 새 토큰 저장
        _token_cache["token"] = data["access_token"]

        # 만료 시각 = 현재 시각 + 23시간
        # (KIS 토큰 유효기간은 24시간이지만, 여유를 두고 23시간으로 설정)
        _token_cache["expires_at"] = now + timedelta(hours=23)

        return data["access_token"]


async def get_current_price(symbol_code: str) -> dict:
    """
    국내 주식 현재가를 조회합니다.

    Args:
        symbol_code: 종목 코드 (예: "005930" = 삼성전자)

    Returns:
        현재가 정보 딕셔너리:
        {
            "symbol_code": "005930",
            "current_price": 75000,   # 현재가 (원)
            "change_rate": 1.5,       # 등락률 (%)
            "high": 76000,            # 당일 고가
            "low": 74500,             # 당일 저가
            "volume": 15000000        # 당일 거래량
        }
    """
    # API 키가 설정되지 않은 경우 안내 메시지 반환 (개발 테스트용)
    if not settings.KIS_APP_KEY:
        return {
            "symbol_code": symbol_code,
            "current_price": 0,
            "change_rate": 0.0,
            "message": "KIS API 키가 설정되지 않았습니다. .env 파일에 KIS_APP_KEY를 입력하세요.",
        }

    # 유효한 토큰 가져오기 (만료됐으면 자동으로 새로 발급)
    token = await get_kis_token()

    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.get(
            f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers={
                "authorization": f"Bearer {token}",
                "appkey": settings.KIS_APP_KEY,
                "appsecret": settings.KIS_APP_SECRET,
                "tr_id": "FHKST01010100",   # KIS API 거래ID (현재가 조회용 고정값)
                "custtype": "P",             # P = 개인, B = 법인 (필수 헤더)
            },
            params={
                "FID_COND_MRKT_DIV_CODE": "J",       # J = 주식시장
                "FID_INPUT_ISCD": symbol_code,         # 조회할 종목 코드
            },
        )
        # KIS 오류 시 응답 내용을 출력해서 디버깅에 활용
        if resp.status_code >= 400:
            print(f"[KIS 오류] status={resp.status_code} body={resp.text}")
        resp.raise_for_status()
        output = resp.json().get("output", {})

        return {
            "symbol_code": symbol_code,
            "current_price": int(output.get("stck_prpr", 0)),    # 현재가
            "change_rate": float(output.get("prdy_ctrt", 0)),    # 전일 대비 등락률(%)
            "high": int(output.get("stck_hgpr", 0)),             # 당일 고가
            "low": int(output.get("stck_lwpr", 0)),              # 당일 저가
            "volume": int(output.get("acml_vol", 0)),            # 누적 거래량
        }


async def get_stock_chart(symbol_code: str, period: str = "D") -> list[dict]:
    """
    주식 차트 데이터(일봉/주봉/월봉)를 조회합니다.

    Args:
        symbol_code: 종목 코드
        period: 조회 주기
            "D" → 일봉 (하루치 데이터)
            "W" → 주봉 (일주일치)
            "M" → 월봉 (한 달치)

    Returns:
        캔들스틱 차트 데이터 리스트:
        [
            {
                "date": "20240115",   # 날짜 (YYYYMMDD 형식)
                "open": 74000,        # 시가
                "high": 76000,        # 고가
                "low": 73500,         # 저가
                "close": 75000,       # 종가
                "volume": 15000000    # 거래량
            },
            ...
        ]
    """
    if not settings.KIS_APP_KEY:
        return []

    token = await get_kis_token()

    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.get(
            f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-price",
            headers={
                "authorization": f"Bearer {token}",
                "appkey": settings.KIS_APP_KEY,
                "appsecret": settings.KIS_APP_SECRET,
                "tr_id": "FHKST01010400",   # KIS API 거래ID (일별 가격 조회용 고정값)
                "custtype": "P",             # P = 개인, B = 법인 (필수 헤더)
            },
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol_code,
                "FID_PERIOD_DIV_CODE": period,   # D(일봉), W(주봉), M(월봉)
                "FID_ORG_ADJ_PRC": "0",          # 0 = 수정주가 미반영
            },
        )
        resp.raise_for_status()
        raw = resp.json()
        # KIS 응답 전체를 로그에 출력 (디버깅용)
        print(f"[KIS 차트 응답] keys={list(raw.keys())} rt_cd={raw.get('rt_cd')} msg={raw.get('msg1')}")
        # KIS 모의투자 차트 API는 output 키로 데이터를 반환함
        output = raw.get("output", [])

        # KIS API 응답 필드명을 우리가 쓰기 편한 이름으로 변환
        return [
            {
                "date": item.get("stck_bsop_date"),          # 영업일자 (YYYYMMDD)
                "open": int(item.get("stck_oprc", 0)),        # 시가
                "high": int(item.get("stck_hgpr", 0)),        # 고가
                "low": int(item.get("stck_lwpr", 0)),         # 저가
                "close": int(item.get("stck_clpr", 0)),       # 종가
                "volume": int(item.get("acml_vol", 0)),       # 누적 거래량
            }
            for item in output
        ]
