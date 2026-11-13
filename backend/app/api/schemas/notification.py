from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict


class NotificationStatus(str, Enum):
    UNREAD = "unread"
    READ = "read"


class InformationNotification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    run_id: str
    event_id: str
    title: str
    text: str
    created_at: datetime
    status: NotificationStatus
    acknowledged_at: datetime | None
