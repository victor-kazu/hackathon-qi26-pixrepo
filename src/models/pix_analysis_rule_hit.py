from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from models.base import Base
from models.pix_analysis import PixAnalysis


class PixAnalysisRuleHit(Base):
    __tablename__ = "pix_analysis_rule_hit"

    id = Column(BigInteger, primary_key=True)
    pix_analysis_id = Column(BigInteger, ForeignKey(PixAnalysis.id), nullable=False)
    rule_code = Column(String(50), nullable=False)
    category = Column(String(20), nullable=False)
    points = Column(Integer, nullable=False)
    is_hard_rule = Column(Boolean, nullable=False)
    detail = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    analysis = relationship("PixAnalysis", back_populates="rule_hits")
