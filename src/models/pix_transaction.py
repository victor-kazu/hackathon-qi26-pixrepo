from sqlalchemy import (
    BigInteger,
    CHAR,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)

from models.base import Base
from models.payer import Payer


class PixTransaction(Base):
    """O Pix exatamente como chegou, em colunas planas.

    Plano, e não um JSON, porque é sobre estas colunas que o motor faz
    as perguntas de histórico — quantos Pix este pagador fez, quantos
    pagadores esta conta recebeu, quantas pessoas usaram este aparelho.
    """

    __tablename__ = "pix_transaction"

    id = Column(Integer, primary_key=True)
    transaction_id = Column(String(100), nullable=False)
    payload_hash = Column(CHAR(64), nullable=False)
    payer_id = Column(Integer, ForeignKey(Payer.id), nullable=False)
    amount = Column(BigInteger, nullable=False)
    transaction_date = Column(DateTime(timezone=True), nullable=False)
    utc_offset_minutes = Column(SmallInteger, nullable=False)

    source_branch = Column(String(10), nullable=False)
    source_account_number = Column(String(20), nullable=False)
    source_opening_date = Column(DateTime(timezone=True), nullable=False)
    source_document_number = Column(String(18), nullable=False)

    destination_participant = Column(CHAR(8), nullable=False)
    destination_branch = Column(String(10), nullable=False)
    destination_account_number = Column(String(20), nullable=False)
    destination_opening_date = Column(DateTime(timezone=True), nullable=False)
    destination_document_number = Column(String(18), nullable=False)
    destination_document_type = Column(String(4), nullable=False)

    device_id = Column(String(100), nullable=False)
    channel = Column(String(30), nullable=False)
    ip = Column(String(45), nullable=False)
    latitude = Column(Numeric(9, 6), nullable=True)
    longitude = Column(Numeric(9, 6), nullable=True)

    received_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("transaction_id"),)
