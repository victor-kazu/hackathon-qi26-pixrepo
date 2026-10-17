import os
import sys
from pathlib import Path

from tests.utils.db_utils import DEFAULT_DATABASE_URL

# As regras são Python puro: os testes de unidade importam o motor
# direto de src/, sem API e sem banco de pé.
SRC_PATH = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_PATH))

# O motor mora em controllers/, e importar o pacote controllers carrega
# o database.py, que exige uma URL. O SQLAlchemy só conecta no primeiro
# uso e os testes de unidade nunca usam. É a mesma URL padrão dos testes
# de integração, porque a variável vale para a suíte inteira.
os.environ.setdefault("DATABASE_URL", DEFAULT_DATABASE_URL)
