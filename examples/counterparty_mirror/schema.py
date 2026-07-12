from pydantic import BaseModel


class TargetRow(BaseModel):
    deal_id: str
    coupon_rate: float
    as_of: str
    counterparty: str
