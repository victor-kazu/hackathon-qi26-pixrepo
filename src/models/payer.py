from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint, func

from models.base import Base


class Payer(Base):
    """Quem paga, identificado pelo documento.

    A linha existe para ser travada. Duas análises do mesmo pagador que
    chegam juntas não podem ler o histórico ao mesmo tempo — senão as duas
    contam o mesmo número de Pix recentes e as duas passam no velocity.
    """

    __tablename__ = "payer"

    id = Column(Integer, primary_key=True)
    document_number = Column(String(18), nullable=False)
    document_type = Column(String(4), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("document_number"),)

    CPF = "CPF"
    CNPJ = "CNPJ"
