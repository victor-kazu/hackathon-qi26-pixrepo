import sys
from pathlib import Path

# As regras são Python puro: os testes de unidade importam o motor
# direto de src/, sem API e sem banco de pé.
SRC_PATH = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_PATH))
