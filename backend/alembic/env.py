"""
alembic/env.py - Alembic 마이그레이션 환경 설정

Alembic이란?
    DB 스키마(테이블 구조)의 변경 이력을 관리하는 도구입니다.
    
    예를 들어:
    - users 테이블에 phone 컬럼 추가
    - orders 테이블의 price 컬럼 타입 변경
    
    이런 변경사항을 "마이그레이션 파일"로 기록하고
    DB에 순서대로 적용할 수 있습니다.

주요 명령어:
    alembic revision --autogenerate -m "설명"
        → 현재 모델과 DB를 비교해서 변경사항을 마이그레이션 파일로 생성
    
    alembic upgrade head
        → 가장 최신 마이그레이션까지 DB에 적용

이 파일은 Alembic이 어떤 DB에 연결하고, 어떤 모델들을 감지할지 설정합니다.
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# 프로젝트 루트를 파이썬 경로에 추가
# 이렇게 해야 "from app.config import settings" 같은 import가 동작함
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.config import settings
from app.database import Base

# 모든 모델을 import해야 Alembic이 테이블을 감지할 수 있음
# models/__init__.py에서 모든 모델을 불러옴
import app.models  # noqa: F401 (사용하지 않는 것처럼 보여도 반드시 필요)

# Alembic 설정 객체
config = context.config

# 로그 설정 (alembic.ini의 로그 설정 적용)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# .env 파일의 DATABASE_URL을 Alembic에 주입
# 이렇게 하면 alembic.ini에 DB 주소를 직접 쓰지 않아도 됨
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# Alembic이 비교할 DB 메타데이터 (어떤 테이블들이 있어야 하는지)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    오프라인 모드 마이그레이션.
    
    실제 DB 연결 없이 SQL 파일만 생성합니다.
    DB가 없는 환경에서 SQL 스크립트를 만들 때 사용합니다.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    온라인 모드 마이그레이션 (일반적으로 사용하는 방식).
    
    실제 DB에 직접 연결해서 마이그레이션을 실행합니다.
    alembic upgrade head 명령어 실행 시 이 함수가 호출됩니다.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # 마이그레이션 후 연결 즉시 해제
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


# 오프라인/온라인 모드 자동 선택
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
