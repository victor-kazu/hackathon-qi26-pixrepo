from sqlalchemy import BigInteger, CHAR, Column, DateTime, Numeric, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from models.base import Base


class PixTransaction(Base):
    __tablename__ = "pix_transaction"

    id = Column(BigInteger, primary_key=True)
    pix_id = Column(String(100), nullable=False)
    amount = Column(BigInteger, nullable=False)
    transaction_date = Column(DateTime(timezone=True), nullable=False)
    utc_offset_minutes = Column(SmallInteger, nullable=False)

    source_branch = Column(String(10), nullable=False)
    source_account_number = Column(String(20), nullable=False)
    source_opening_date = Column(DateTime(timezone=True), nullable=False)
    source_document_number = Column(String(14), nullable=False)

    destination_participant = Column(CHAR(8), nullable=False)
    destination_branch = Column(String(10), nullable=False)
    destination_account_number = Column(String(20), nullable=False)
    destination_opening_date = Column(DateTime(timezone=True), nullable=False)
    destination_document_number = Column(String(14), nullable=False)

    device_id = Column(String(100), nullable=False)
    channel = Column(String(30), nullable=False)
    ip = Column(String(45), nullable=False)
    latitude = Column(Numeric(9, 6), nullable=True)
    longitude = Column(Numeric(9, 6), nullable=True)

    raw_payload = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("pix_id"),)

    analysis = relationship("PixAnalysis", back_populates="pix_transaction", uselist=False)
