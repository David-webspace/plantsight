"""測試直接對本機資料庫跑清理（會整批重寫資料表）；每個測試前清掉待確認的決定，從原始檔重跑。"""

import json

import pytest
from sqlalchemy import delete

from app.config import settings
from app.db import SessionLocal
from app.etl.clean import run_cleaning
from app.models import ReviewItem


@pytest.fixture
def db():
    with SessionLocal() as s:
        s.execute(delete(ReviewItem))
        s.commit()
        yield s
        s.execute(delete(ReviewItem))
        s.commit()
        run_cleaning(s)


@pytest.fixture
def report(db):
    return run_cleaning(db)


@pytest.fixture
def manifest():
    return json.loads((settings.data_dir / "raw" / "manifest.json").read_text())
