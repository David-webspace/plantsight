"""python -m app.agent.cli --user tw.manager "上個月良率最差的是哪條線？"：在終端機直接問，開發與示範用。"""

import argparse
import json

from app.agent.llm import get_client
from app.agent.loop import run_chat
from app.auth import load_principal
from app.db import SessionLocal


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--user", default="gm")
    ap.add_argument("--conv", type=int, help="接續的對話編號（追問）")
    a = ap.parse_args()
    with SessionLocal() as db:
        r = run_chat(db, get_client(), load_principal(db, a.user), a.question, a.conv)
    print(r.answer)
    print(json.dumps({"conversation": r.conversation_id, "model": r.model, "tools": r.tools, "grounding_ok": r.grounding_ok,
                      "escalated": r.escalated, "latency_ms": r.latency_ms, "cost_usd": r.cost_usd}, ensure_ascii=False))


if __name__ == "__main__":
    main()
