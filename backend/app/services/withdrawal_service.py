"""Delete a member and dependent simulation data in one transaction."""
from fastapi import HTTPException
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Account, AiPropensityAdvice, Comment, Follow, Order, OrderAutomation,
    Portfolio, Post, PostLike, User, UserSurveyResponse,
)
from app.models.misu import MisuDebt, MisuPayment, MisuSettlement
from app.models.community import PostVote
from app.utils.security import verify_password


def withdraw_user(db: Session, user_id: int, passwd: str) -> None:
    try:
        user = db.execute(select(User).where(User.user_id == user_id).with_for_update()).scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=401, detail="이미 탈퇴했거나 로그인 정보가 만료되었습니다.")
        if not verify_password(passwd, user.passwd):
            # A bad confirmation password must not invalidate the current session.
            raise HTTPException(status_code=400, detail="현재 비밀번호가 일치하지 않습니다.")

        account_ids = list(db.scalars(select(Account.account_id).where(Account.user_id == user_id).order_by(Account.account_id).with_for_update()))
        for model in (OrderAutomation, MisuDebt, MisuSettlement, MisuPayment, Portfolio, Order):
            db.execute(delete(model).where(model.account_id.in_(account_ids)))
        db.execute(delete(Account).where(Account.user_id == user_id))

        post_ids = select(Post.post_id).where(Post.user_id == user_id)
        # Replies and likes on removed posts must also be removed to satisfy FKs.
        for model in (Comment, PostLike, PostVote):
            db.execute(delete(model).where(or_(model.user_id == user_id, model.post_id.in_(post_ids))))
        db.execute(delete(Post).where(Post.user_id == user_id))
        db.execute(delete(Follow).where(or_(Follow.follower_id == user_id, Follow.following_id == user_id)))
        for model in (UserSurveyResponse, AiPropensityAdvice):
            db.execute(delete(model).where(model.user_id == user_id))
        db.execute(delete(User).where(User.user_id == user_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
