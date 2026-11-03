from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, Numeric, UniqueConstraint
from app.database import Base


class AssetHistory(Base):
    __tablename__ = 'asset_history'
    __table_args__ = (UniqueConstraint('account_id', 'date', name='uq_asset_history_account_date'),)

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('account.account_id', ondelete='CASCADE'), nullable=False)
    date = Column(Date, nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    cash = Column(Numeric(20, 2), nullable=False)
    market = Column(Numeric(20, 2), nullable=False)
    debt = Column(Numeric(20, 2), nullable=False)
    pending_proceeds = Column(Numeric(20, 2), nullable=False)
    total = Column(Numeric(20, 2), nullable=False)
    is_demo = Column(Boolean, nullable=False, default=False, server_default='false')
