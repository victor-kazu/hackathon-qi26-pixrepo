from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint, func

from models.base import Base


class PixAnalysisStatus(Base):
    __tablename__ = "pix_analysis_status"

    id = Column(Integer, primary_key=True)
    enumerator = Column(String(30), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("enumerator"),)

    # Os mesmos textos da policy do motor e das linhas do database.sql.
    APPROVED = "approved"
    TWO_FACTOR_REQUIRED = "two_factor_required"
    REJECTED = "rejected"
