from sqlalchemy import Column, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from models.base import Base
from models.pix_analysis import PixAnalysis


class PixRuleHit(Base):
    """Uma regra que disparou numa análise, com o dado que a fez disparar.

    É o que permite responder, meses depois, por que um Pix foi barrado:
    qual regra, quantos pontos, e o número exato que ela viu.
    """

    __tablename__ = "pix_rule_hit"

    id = Column(Integer, primary_key=True)
    pix_analysis_id = Column(Integer, ForeignKey(PixAnalysis.id), nullable=False)
    rule_code = Column(String(50), nullable=False)
    points = Column(Integer, nullable=False)
    forced_status = Column(String(20), nullable=True)
    detail = Column(JSONB, nullable=False)

    __table_args__ = (UniqueConstraint("pix_analysis_id", "rule_code"),)

    analysis = relationship("PixAnalysis", back_populates="rule_hits")
