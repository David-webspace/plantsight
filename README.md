# plantsight — 製造業數據問答助理（開發中）

用一句話問生產數據：良率、不良率、準交率、進料品質，回答附口徑、圖表，可以追問與匯出。模型只輸出結構化查詢規格，指標怎麼算、權限怎麼套，全部在語意層的程式裡。

目前進度：M1 合成資料與清理管線、M2 語意層（指標目錄、查詢編譯器、權限）、M3 Agent（查詢、追問、變化拆解）。可用 `uv run python -m app.agent.cli --user tw.manager "問題"` 在終端機提問。

```bash
cp .env.example .env            # 填 POSTGRES_PASSWORD，並同步到 DATABASE_URL
docker compose up -d
cd api && uv sync
set -a && . ../.env && set +a
uv run alembic upgrade head
uv run python -m app.seed.generate   # 產生 data/raw/（含刻意放入的髒資料）
uv run python -m app.etl.clean       # 清理並寫入資料庫，產生 data/CLEANING_REPORT.md
uv run pytest
```

資料規則見 [DATA_RULES.md](DATA_RULES.md)。公司、產品、客戶、供應商全部虛構（Arcwell Electronics）。
