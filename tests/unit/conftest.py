"""Os testes unitários chamam as regras direto no Python, sem API nem banco.

São a exceção ao "não importa nada de src/" dos testes black box: aqui o
objetivo é justamente testar a função sozinha, rápido e com precisão —
cada limiar exatamente no ponto em que muda de lado.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
