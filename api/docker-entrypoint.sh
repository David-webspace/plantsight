#!/bin/sh
set -e

alembic upgrade head

# 第一次啟動：產生示範資料並清理入庫（之後重啟沿用既有資料與待確認決定）
if [ ! -f "$DATA_DIR/raw/manifest.json" ]; then
  python -m app.seed.generate
fi
python - <<'PY'
import subprocess, sys
from sqlalchemy import text
from app.db import engine

with engine.connect() as c:
    empty = c.execute(text("SELECT NOT EXISTS (SELECT 1 FROM work_orders)")).scalar()
if empty:
    subprocess.run([sys.executable, "-m", "app.etl.clean"], check=True, stdout=subprocess.DEVNULL)
PY

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
