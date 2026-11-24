from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from src.constants.enums import Review

class Request(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')

class Credentials(Request):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)

class Upload(Request):
    name: str = Field(min_length=1, max_length=120)
    filename: str = Field(min_length=1, max_length=160)
    csv_text: str = Field(min_length=1, max_length=2_000_000)

class RunRequest(Request):
    dataset_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=120)
    params: dict = Field(default_factory=dict)

class ReviewRequest(Request):
    review: Review
    note: str = Field(min_length=1, max_length=500)
    decision: str = Field(default='', max_length=120)

class ScheduleRequest(RunRequest):
    recipient: str = Field(min_length=1, max_length=100)
    first_at: datetime
    repeat_daily: bool = False


class RecallEventRequest(Request):
    """入库只登记签核记录、库位、批号和件数；调拨只登记起止库位、件数和时间。"""
    kind: Literal['inbound', 'transfer']
    quantity: int = Field(gt=0, le=1_000_000)
    occurred_at: datetime
    request_key: str = Field(min_length=4, max_length=100, pattern=r'^[A-Za-z0-9._:-]+$')
    location: str | None = Field(default=None, min_length=1, max_length=40)
    batch_no: str | None = Field(default=None, min_length=1, max_length=80)
    signoff_id: int | None = Field(default=None, gt=0)
    from_location: str | None = Field(default=None, min_length=1, max_length=40)
    to_location: str | None = Field(default=None, min_length=1, max_length=40)


class SignoffRequest(Request):
    code: str = Field(min_length=1, max_length=60)
    title: str = Field(min_length=1, max_length=120)
    run_id: int | None = Field(default=None, gt=0)
    note: str = Field(default='', max_length=500)


class VoidRequest(Request):
    reason: str = Field(min_length=1, max_length=200)
