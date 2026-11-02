"""브리핑 저장 오류는 생성·조회 응답으로 전파하지 않는다."""
from datetime import datetime, timezone
import logging
from sqlalchemy import delete, select
from app.database import SessionLocal
from app.models.ai_brief_result import AiBriefResult
from app.models.account import Account

logger = logging.getLogger(__name__)


def _failed(db, operation):
    try:
        db.rollback()
    except Exception:
        pass
    # 연결 문자열이나 payload가 포함될 수 있는 원본 예외는 기록하지 않는다.
    logger.warning("brief_store operation=%s failed", operation)


def load_stock(code):
    try:
        with SessionLocal() as db:
            row = db.scalars(select(AiBriefResult).where(
                AiBriefResult.kind == 'stock', AiBriefResult.symbol_code == code,
                AiBriefResult.user_id.is_(None), AiBriefResult.account_id.is_(None)
            ).order_by(AiBriefResult.created_at.desc(), AiBriefResult.id.desc()).limit(1)).first()
            return (row.payload, row.created_at) if row else None
    except Exception:
        logger.warning("brief_store operation=load_stock failed")
        return None


def save_stock(code, payload):
    try:
        with SessionLocal() as db:
            try:
                db.execute(delete(AiBriefResult).where(AiBriefResult.kind == 'stock', AiBriefResult.symbol_code == code))
                db.add(AiBriefResult(kind='stock', symbol_code=code, payload=payload, created_at=datetime.now(timezone.utc)))
                db.commit()
            except Exception:
                _failed(db, 'save_stock')
    except Exception:
        logger.warning("brief_store operation=save_stock failed")


def _scope(user_id, account_id, code):
    return (AiBriefResult.kind == 'portfolio', AiBriefResult.user_id == user_id,
            AiBriefResult.account_id == account_id, AiBriefResult.symbol_code == code)


def save_portfolio(db, user_id, account_id, code, payload):
    try:
        # 같은 계좌의 동시 저장이 최근 2개 보존 경계를 넘지 않도록 직렬화한다.
        db.execute(select(Account.account_id).where(Account.account_id == account_id,
                                                  Account.user_id == user_id).with_for_update())
        db.add(AiBriefResult(kind='portfolio', user_id=user_id, account_id=account_id,
                            symbol_code=code, payload=payload, created_at=datetime.now(timezone.utc)))
        db.flush()
        obsolete = select(AiBriefResult.id).where(*_scope(user_id, account_id, code)).order_by(
            AiBriefResult.created_at.desc(), AiBriefResult.id.desc()).offset(2)
        db.execute(delete(AiBriefResult).where(AiBriefResult.id.in_(obsolete)), execution_options={'synchronize_session': False})
        db.commit()
    except Exception:
        _failed(db, 'save_portfolio')


def list_portfolio(db, user_id, account_id, code):
    try:
        return list(db.scalars(select(AiBriefResult.payload).where(*_scope(user_id, account_id, code)).order_by(
            AiBriefResult.created_at.desc(), AiBriefResult.id.desc()).limit(2)))
    except Exception:
        _failed(db, 'list_portfolio')
        return []
