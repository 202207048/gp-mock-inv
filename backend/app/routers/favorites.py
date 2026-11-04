from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User
from app.models.favorites import FavoriteLibrary
from app.schemas.favorites import FavoriteLibraryData
from app.utils.deps import get_current_user

router = APIRouter()


def snapshot(row):
    return FavoriteLibraryData(revision=row.revision, codes=row.codes, groups=row.groups) if row else FavoriteLibraryData()


@router.get('', response_model=FavoriteLibraryData)
def get_favorites(response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    return snapshot(db.get(FavoriteLibrary, user.user_id))


@router.put('', response_model=FavoriteLibraryData)
def save_favorites(payload: FavoriteLibraryData, response: Response,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    # Lock the owner even for the first save, when there is no library row yet.
    db.execute(select(User.user_id).where(User.user_id == user.user_id).with_for_update()).scalar_one()
    row = db.get(FavoriteLibrary, user.user_id)
    if payload.revision != (row.revision if row else 0):
        db.rollback()
        raise HTTPException(409, '다른 기기에서 관심종목이 변경되었습니다. 다시 시도해 주세요.')
    if row is None:
        row = FavoriteLibrary(user_id=user.user_id)
        db.add(row)
    row.revision = payload.revision + 1
    row.codes = payload.codes
    row.groups = [group.model_dump() for group in payload.groups]
    db.commit()
    db.refresh(row)
    return snapshot(row)
