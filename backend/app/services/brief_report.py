"""브리핑 신고 저장. 결과 원문은 요청 본문이 아니라 서버 저장본에서 가져온다."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from app.models.account import Account
from app.models.ai_brief_report import AiBriefReport
from app.models.ai_brief_result import AiBriefResult
from app.services import brief_store

DAILY_LIMIT = 20


class ReportRejected(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _same_time(payload, generated_at):
    try:
        return datetime.fromisoformat(str(payload.get('generated_at'))) == generated_at
    except (TypeError, ValueError, AttributeError):
        return False


def _portfolio_payload(db, user_id, account_id, code, generated_at):
    rows = db.scalars(select(AiBriefResult.payload).where(
        AiBriefResult.kind == 'portfolio', AiBriefResult.user_id == user_id,
        AiBriefResult.account_id == account_id, AiBriefResult.symbol_code == code))
    return next((payload for payload in rows if _same_time(payload, generated_at)), None)


def _stock_payload(code, generated_at, cached=None):
    # 메모리에 올라온 현재 브리핑이 먼저, 없으면 DB 저장본을 본다.
    if cached is not None and _same_time(cached, generated_at):
        return cached
    stored = brief_store.load_stock(code)
    if stored and _same_time(stored[0], generated_at):
        return stored[0]
    return None


def save_report(db, user_id, body, cached_stock=None):
    if body.kind == 'portfolio':
        if body.account_id is None:
            raise ReportRejected(422, "계좌 번호가 필요합니다.")
        owned = db.scalar(select(Account.account_id).where(Account.account_id == body.account_id,
                                                         Account.user_id == user_id))
        if owned is None:
            raise ReportRejected(404, "계좌를 찾을 수 없습니다.")
        payload = _portfolio_payload(db, user_id, body.account_id, body.symbol_code, body.generated_at)
    else:
        payload = _stock_payload(body.symbol_code, body.generated_at, cached_stock)
    if payload is None:
        raise ReportRejected(404, "신고할 브리핑을 찾을 수 없습니다. 새로고침 후 다시 시도해 주세요.")
    now = datetime.now(timezone.utc)
    recent = db.scalar(select(func.count()).select_from(AiBriefReport).where(
        AiBriefReport.user_id == user_id, AiBriefReport.created_at >= now - timedelta(days=1)))
    if recent >= DAILY_LIMIT:
        raise ReportRejected(429, "오늘은 더 이상 신고할 수 없습니다.")
    report = AiBriefReport(user_id=user_id, account_id=body.account_id if body.kind == 'portfolio' else None,
                           kind=body.kind, symbol_code=body.symbol_code, brief_generated_at=body.generated_at,
                           reason=body.reason, note=body.note or None, payload=payload, created_at=now)
    db.add(report)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ReportRejected(409, "이미 신고한 브리핑입니다.") from None
    return report.id, now
