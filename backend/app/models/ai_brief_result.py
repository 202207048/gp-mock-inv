"""공용 종목 및 사용자별 보유종목 브리핑 저장본."""
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String
from app.database import Base

class AiBriefResult(Base):
    __tablename__ = "ai_brief_results"
    id = Column(Integer, primary_key=True)
    kind = Column(String(20), nullable=False)
    symbol_code = Column(String(12), nullable=False)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    account_id = Column(Integer, ForeignKey("account.account_id"), nullable=True)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index("ix_ai_brief_results_scope_created", "kind", "symbol_code", "user_id", "account_id", "created_at"),)
