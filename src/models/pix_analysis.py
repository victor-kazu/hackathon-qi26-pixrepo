from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from models.base import Base
from models.pix_transaction import PixTransaction


class PixAnalysis(Base):
    """A decisão sobre um Pix. Uma só por Pix — o UNIQUE barra a segunda."""

    __tablename__ = "pix_analysis"

    id = Column(Integer, primary_key=True)
    analysis_key = Column(UUID(as_uuid=True), nullable=False)
    pix_transaction_id = Column(Integer, ForeignKey(PixTransaction.id), nullable=False)
    status = Column(String(20), nullable=False)
    score = Column(Integer, nullable=False)
    # "score" quando a soma de pontos decidiu; o código da regra dura quando foi ela.
    decided_by = Column(String(50), nullable=False)
    rules_version = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("analysis_key"),
        UniqueConstraint("pix_transaction_id"),
    )

    rule_hits = relationship("PixRuleHit", back_populates="analysis", order_by="PixRuleHit.id", lazy="selectin")

    APPROVED = "approved"
    TWO_FACTOR_REQUIRED = "two_factor_required"
    REJECTED = "rejected"
