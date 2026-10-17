from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint, func

from engine.policy import APPROVED, REJECTED, TWO_FACTOR_REQUIRED
from models.base import Base


class PixAnalysisStatus(Base):
    __tablename__ = "pix_analysis_status"

    id = Column(Integer, primary_key=True)
    enumerator = Column(String(30), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("enumerator"),)

    APPROVED = APPROVED
    TWO_FACTOR_REQUIRED = TWO_FACTOR_REQUIRED
    REJECTED = REJECTED
