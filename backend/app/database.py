"""
database.py - 데이터베이스 연결 설정 파일

PostgreSQL 데이터베이스와 파이썬 코드를 연결해주는 설정입니다.
SQLAlchemy라는 ORM 라이브러리를 사용합니다.

ORM이란? (Object Relational Mapping)
  SQL을 직접 쓰지 않고 파이썬 클래스/객체로 DB를 조작할 수 있게 해주는 도구
  예: SQL → SELECT * FROM users WHERE user_id = 1
      ORM → db.query(User).filter(User.user_id == 1).first()
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings


# 데이터베이스 엔진 생성
# 엔진은 실제 DB와의 연결을 담당하는 객체입니다
# settings.DATABASE_URL에 있는 접속 정보를 사용합니다
engine = create_engine(settings.DATABASE_URL)


# DB 세션 팩토리 생성
# 세션은 DB와 대화하는 창구입니다
# - autocommit=False: 내가 직접 commit()을 호출해야 DB에 반영됨 (실수 방지)
# - autoflush=False: 자동으로 SQL을 보내지 않음
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# 모든 DB 모델 클래스가 상속받을 기본 클래스
# 예: class User(Base): 처럼 사용
Base = declarative_base()


def get_db():
    """
    FastAPI 의존성 주입용 DB 세션 생성 함수.
    
    API 함수가 호출될 때마다 DB 세션을 하나 만들어주고,
    API 처리가 끝나면 자동으로 세션을 닫아줍니다.
    
    사용 예시 (라우터에서):
        def some_api(db: Session = Depends(get_db)):
            users = db.query(User).all()  # DB에서 모든 유저 조회
    
    yield: 세션을 API 함수에 넘겨주고, 함수가 끝나면 finally로 돌아와 닫음
    """
    db = SessionLocal()
    try:
        yield db  # API 함수에서 이 db 객체를 받아서 사용
    finally:
        db.close()  # API 처리 완료 후 반드시 세션 닫기 (메모리 누수 방지)
