"""Simulation-only settlement obligations and delayed sale proceeds."""
from sqlalchemy import Column, Integer, String, Numeric, DateTime, ForeignKey, Boolean, UniqueConstraint
from app.database import Base


class MisuDebt(Base):
    __tablename__ = 'misu_debts'
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False, index=True)
    order_id = Column(Integer, ForeignKey('orders.order_id'), nullable=False, unique=True)
    request_id = Column(String(36), nullable=False)
    original = Column(Numeric(20, 2), nullable=False)
    remaining = Column(Numeric(20, 2), nullable=False)
    due_at = Column(DateTime(timezone=True), nullable=False)
    liquidate_at = Column(DateTime(timezone=True), nullable=False)
    frozen_until = Column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint('account_id', 'request_id', name='uq_misu_request'),)


class MisuSettlement(Base):
    __tablename__ = 'misu_settlements'
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False, index=True)
    order_id = Column(Integer, ForeignKey('orders.order_id'), nullable=False, unique=True)
    amount = Column(Numeric(20, 2), nullable=False)
    available_at = Column(DateTime(timezone=True), nullable=False)
    applied = Column(Boolean, nullable=False, default=False)


class MisuPayment(Base):
    __tablename__ = 'misu_payments'
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False)
    request_id = Column(String(36), nullable=False)
    amount = Column(Numeric(20, 2), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint('account_id', 'request_id', name='uq_misu_payment'),)
