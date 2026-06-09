from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import relationship

from app.database import Base


class AiPropensityAdvice(Base):
    """AI 투자 성향 분석 결과 테이블."""

    __tablename__ = "ai_propensity_advice"

    advice_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    ai_analysis_result = Column(Text, nullable=True)
    ai_detailed_advice = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="ai_advices")
