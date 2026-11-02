"""사용자가 이상하다고 신고한 AI 브리핑과 당시 결과 원문."""
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from app.database import Base

class AiBriefReport(Base):
    __tablename__ = "ai_brief_reports"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    account_id = Column(Integer, ForeignKey("account.account_id"), nullable=True)
    kind = Column(String(20), nullable=False)
    symbol_code = Column(String(12), nullable=False)
    brief_generated_at = Column(DateTime(timezone=True), nullable=False)
    reason = Column(String(20), nullable=False)
    note = Column(String(200), nullable=True)
    # 신고 시점에 서버 저장본에서 찾은 결과 원문
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "symbol_code", "brief_generated_at", name="uq_ai_brief_reports_once"),
        Index("ix_ai_brief_reports_user_created", "user_id", "created_at"),
    )
