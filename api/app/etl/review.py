"""待確認清單的決定。核准會讓系統建議真的生效：寫下決定後從原始檔重跑清理，建議在清理時套用。"""

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import TZ
from app.etl.clean import run_cleaning
from app.models import ReviewItem


class ReviewError(Exception):
    pass


def decide(db: Session, item_id: str, approve: bool, by: str) -> dict:
    item = db.get(ReviewItem, item_id)
    if item is None:
        raise ReviewError("找不到這個待確認項目")
    if item.status != "pending":
        raise ReviewError("這個項目已經決定過")
    if approve and item.suggestion is None:
        raise ReviewError("這個項目沒有可套用的建議，只能駁回（維持排除）")
    db.execute(update(ReviewItem).where(ReviewItem.id == item_id).values(
        status="approved" if approve else "rejected", decided_by=by, decided_at=datetime.now(TZ)))
    db.commit()
    return run_cleaning(db)
