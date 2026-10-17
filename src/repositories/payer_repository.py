from sqlalchemy.dialects.postgresql import insert

from database import Context
from models import Payer


class PayerRepository:
    def __init__(self, context: Context) -> None:
        self.session = context.db_session

    def lock(self, document_number: str, document_type: str) -> Payer:
        """Devolve o pagador TRAVADO até o fim da transação, criando se for o primeiro Pix dele.

        Dois passos, e a ordem importa:

        1. `INSERT … ON CONFLICT DO NOTHING`: garante que a linha existe sem
           estourar quando dois primeiros Pix do mesmo pagador chegam juntos
           — o segundo INSERT espera o primeiro e não faz nada.
        2. `SELECT … FOR UPDATE`: a trava. Outra análise do mesmo pagador
           fica parada aqui até esta dar commit, e só então lê o histórico
           — que já inclui o Pix desta.
        """
        self.session.execute(
            insert(Payer)
            .values(document_number=document_number, document_type=document_type)
            .on_conflict_do_nothing(index_elements=["document_number"])
        )

        return (
            self.session.query(Payer)
            .filter(Payer.document_number == document_number)
            .with_for_update()
            .one()
        )
