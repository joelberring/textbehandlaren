from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from firebase_admin import firestore

from backend.app.core.config import settings
from backend.app.core.firebase import db


@dataclass
class QuotaExceededError(Exception):
    message: str
    retry_after_seconds: int


class QuotaService:
    def _counter_ref(self, scope: str, key: str, window: str, bucket: str):
        doc_id = f"{scope}:{key}:{window}:{bucket}"
        return db.collection("usage_counters").document(doc_id)

    def _tick_counter(self, ref, expires_at: datetime) -> int:
        ref.set(
            {
                "count": firestore.Increment(1),
                "updated_at": datetime.utcnow(),
                "expires_at": expires_at,
            },
            merge=True
        )
        snap = ref.get()
        if not snap.exists:
            return 1
        data = snap.to_dict() or {}
        try:
            return int(data.get("count", 1))
        except Exception:
            return 1

    def _seconds_to_next_minute(self, now: datetime) -> int:
        return max(1, int((now.replace(second=0, microsecond=0) + timedelta(minutes=1) - now).total_seconds()))

    def _seconds_to_next_day(self, now: datetime) -> int:
        next_day = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(1, int((next_day - now).total_seconds()))

    def _enforce_scope(self, scope: str, key: str, per_minute: int, per_day: int):
        now = datetime.utcnow()
        minute_bucket = now.strftime("%Y%m%d%H%M")
        day_bucket = now.strftime("%Y%m%d")

        minute_ref = self._counter_ref(scope, key, "m", minute_bucket)
        minute_count = self._tick_counter(
            minute_ref,
            now.replace(second=0, microsecond=0) + timedelta(minutes=2)
        )
        if per_minute > 0 and minute_count > per_minute:
            raise QuotaExceededError(
                message=f"För många förfrågningar för {scope} senaste minuten ({minute_count}/{per_minute}).",
                retry_after_seconds=self._seconds_to_next_minute(now)
            )

        day_ref = self._counter_ref(scope, key, "d", day_bucket)
        day_count = self._tick_counter(
            day_ref,
            (now + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
        )
        if per_day > 0 and day_count > per_day:
            raise QuotaExceededError(
                message=f"Daglig kvot uppnådd för {scope} ({day_count}/{per_day}).",
                retry_after_seconds=self._seconds_to_next_day(now)
            )

    def enforce_chat_quotas(self, user_id: str, project_id: Optional[str] = None):
        if not settings.CHAT_RATE_LIMIT_ENABLED:
            return

        self._enforce_scope(
            "user",
            user_id,
            settings.CHAT_RATE_LIMIT_USER_PER_MINUTE,
            settings.CHAT_DAILY_USER_QUOTA
        )

        if project_id:
            self._enforce_scope(
                "project",
                project_id,
                settings.CHAT_RATE_LIMIT_PROJECT_PER_MINUTE,
                settings.CHAT_DAILY_PROJECT_QUOTA
            )


quota_service = QuotaService()

