from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import relationship

from app.database import Base


class UserSurveyResponse(Base):
    """투자 성향 설문 답변 테이블."""

    __tablename__ = "user_survey_response"

    response_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    question_number = Column(Integer, nullable=False)
    selected_answer = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="survey_responses")
