from sqlalchemy import BigInteger, CHAR, Column, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from models.base import Base
from models.pix_analysis_status import PixAnalysisStatus
from models.pix_transaction import PixTransaction


class PixAnalysis(Base):
    __tablename__ = "pix_analysis"

    id = Column(BigInteger, primary_key=True)
    analysis_key = Column(CHAR(36), nullable=False)
    pix_transaction_id = Column(BigInteger, ForeignKey(PixTransaction.id), nullable=False)
    status_id = Column(Integer, ForeignKey(PixAnalysisStatus.id), nullable=False)
    risk_score = Column(Integer, nullable=False)
    identity_score = Column(Integer, nullable=False)
    beneficiary_score = Column(Integer, nullable=False)
    decision_reason = Column(Text, nullable=False)
    policy_version = Column(String(30), nullable=False)
    processing_time_ms = Column(Numeric(10, 3), nullable=False)
    analysis_metadata = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("analysis_key"),
        UniqueConstraint("pix_transaction_id"),
    )

    status = relationship("PixAnalysisStatus", foreign_keys=[status_id], lazy="selectin")
    pix_transaction = relationship("PixTransaction", back_populates="analysis", lazy="selectin")
    rule_hits = relationship("PixAnalysisRuleHit", back_populates="analysis")
