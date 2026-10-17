"""Simulação de tráfego: clientes bons + ataques, e o que o motor decidiu.

Gera 60 dias de histórico de clientes comuns (aluguel, mercado, amigos)
e, no último dia, mistura o uso normal com sete tipos de fraude. Manda
tudo para a API em ordem cronológica e mede, por cenário, quanto foi
aprovado, desafiado com 2FA ou rejeitado.

Os rótulos de fraude são do próprio script: é um teste de consistência
da política contra ataques conhecidos, não uma medida de precisão real
(para isso seria preciso fraude confirmada em produção).

Uso, com a API de pé:

    python scripts/simulate.py                # 150 clientes
    python scripts/simulate.py --customers 300 --seed 7
"""

import argparse
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from os import environ
from pathlib import Path
from uuid import uuid4

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.utils.random_generator import RandomGenerator  # noqa: E402

BRASILIA = timezone(timedelta(hours=-3))
API_URL = f"http://{environ.get('SERVER_LOCALHOST', 'localhost')}:{environ.get('API_PORT', '3000')}"
HEADERS = {"INTERNAL-TOKEN": environ.get("INTERNAL_TOKEN", "default_token")}

LEGIT = "legítimo"
SCENARIOS_FRAUD = [
    "tomada de conta (aparelho novo)",
    "celular roubado à noite",
    "golpe para conta laranja",
    "robô em rajada",
    "fracionamento",
    "fazenda de aparelhos",
    "fracionamento noturno",
]


def iso(moment):
    return moment.isoformat(timespec="seconds")


def account(participant=None, opened=None, document=None):
    data = {
        "branch": "0001",
        "account_number": str(random.randrange(10_000_000, 99_999_999)),
        "opening_date": iso(opened),
        "owner": {"document_number": document or RandomGenerator.generate_cpf()},
    }
    if participant:
        data["participant"] = participant
    return data


def pix(moment, amount, customer, destination, device=None, geo=True):
    source = {"device_id": device or customer["device"], "channel": "app", "ip": customer["ip"]}
    if geo:
        source["geolocation"] = customer["home"]
    return {
        "id": f"sim-{uuid4()}",
        "amount": int(amount),
        "transaction_date": iso(moment),
        "source_account": customer["account"],
        "destination_account": destination,
        "source": source,
    }


def build_population(customers_count, eval_day):
    start = eval_day - timedelta(days=60)
    cities = [(-23.56, -46.65), (-22.90, -43.20), (-19.92, -43.94), (-30.03, -51.23), (-8.05, -34.88)]
    customers = []
    for _ in range(customers_count):
        lat, lon = random.choice(cities)
        customer = {
            "account": account(opened=start - timedelta(days=random.randint(90, 2000))),
            "device": f"dvc-{uuid4().hex[:10]}",
            "ip": f"177.{random.randrange(256)}.{random.randrange(256)}.{random.randrange(1, 255)}",
            "home": {"latitude": lat + random.uniform(-0.1, 0.1), "longitude": lon + random.uniform(-0.1, 0.1)},
            # Ticket típico do cliente: de R$ 30 a R$ 400.
            "ticket": random.uniform(3_000, 40_000),
            "payees": [account("60701190", start - timedelta(days=random.randint(200, 3000))) for _ in range(5)],
            "landlord": account("00360305", start - timedelta(days=1500)),
            "rent": random.choice([90_000, 150_000, 250_000, 400_000]),
        }
        customers.append(customer)
    return customers


def daytime(day, hour_from=8, hour_to=20):
    return day.replace(hour=random.randint(hour_from, hour_to - 1), minute=random.randint(0, 59), second=random.randint(0, 59))


def generate_history(customers, eval_day):
    events = []
    for customer in customers:
        for days_ago in range(60, 0, -1):
            day = eval_day - timedelta(days=days_ago)
            if random.random() < 0.35:
                amount = customer["ticket"] * random.lognormvariate(0, 0.5)
                events.append((pix(daytime(day), amount, customer, random.choice(customer["payees"])), LEGIT, False))
            if days_ago in (55, 25):
                events.append((pix(daytime(day, 9, 12), customer["rent"], customer, customer["landlord"]), LEGIT, False))
    return events


def generate_eval_day(customers, eval_day):
    events = []
    random.shuffle(customers)
    victims = customers[: len(customers) // 3]

    for customer in customers:
        # Uso normal do dia: pagar alguém de sempre, às vezes alguém novo, às vezes à noite.
        for _ in range(random.randint(1, 3)):
            roll = random.random()
            if roll < 0.6:
                destination = random.choice(customer["payees"])
                moment = daytime(eval_day)
                amount = customer["ticket"] * random.lognormvariate(0, 0.5)
            elif roll < 0.85:
                destination = account("60701190", eval_day - timedelta(days=random.randint(10, 3000)))
                moment = daytime(eval_day)
                amount = customer["ticket"] * random.lognormvariate(0.3, 0.6)
            else:
                destination = random.choice(customer["payees"])
                moment = daytime(eval_day, 20, 24)
                amount = customer["ticket"] * random.lognormvariate(0, 0.5)
            events.append((pix(moment, amount, customer, destination), LEGIT, True))
        if random.random() < 0.3:
            events.append((pix(daytime(eval_day, 9, 12), customer["rent"], customer, customer["landlord"]), LEGIT, True))

    chunk = max(1, len(victims) // 6)

    # Tomada de conta: golpista entra de outro aparelho e manda para destino novo.
    for customer in victims[0:chunk]:
        attacker_device = f"dvc-atk-{uuid4().hex[:8]}"
        start = daytime(eval_day)
        for step in range(3):
            destination = account("60701190", eval_day - timedelta(days=random.randint(1, 60)))
            events.append((pix(start + timedelta(minutes=4 * step), random.uniform(50_000, 300_000), customer, destination, attacker_device, geo=False),
                           SCENARIOS_FRAUD[0], True))

    # Celular roubado e destravado à noite: mesmo aparelho, valor alto, destino novo.
    for customer in victims[chunk:2 * chunk]:
        start = daytime(eval_day, 21, 24)
        for step in range(3):
            destination = account("60701190", eval_day - timedelta(days=random.randint(1, 20)))
            events.append((pix(start + timedelta(minutes=3 * step), random.uniform(80_000, 400_000), customer, destination),
                           SCENARIOS_FRAUD[1], True))

    # Engenharia social: vítimas no próprio aparelho pagando 3 contas laranja recém-abertas.
    mules = [account("60701190", eval_day - timedelta(days=random.randint(1, 5))) for _ in range(3)]
    for index, customer in enumerate(victims[2 * chunk:3 * chunk + chunk]):
        moment = eval_day.replace(hour=14) + timedelta(minutes=7 * index)
        amount = customer["ticket"] * random.uniform(3, 15)
        events.append((pix(moment, amount, customer, mules[index % 3]), SCENARIOS_FRAUD[2], True))

    # Robô: dezenas de Pix pequenos em sequência para esvaziar a conta.
    for customer in victims[4 * chunk:5 * chunk]:
        start = daytime(eval_day)
        destination = account("60701190", eval_day - timedelta(days=30))
        for step in range(8):
            events.append((pix(start + timedelta(seconds=20 * step), random.uniform(5_000, 20_000), customer, destination),
                           SCENARIOS_FRAUD[3], True))

    # Fracionamento: o mesmo valor várias vezes para ficar abaixo dos limites.
    for customer in victims[5 * chunk:6 * chunk]:
        start = daytime(eval_day)
        amount = random.choice([49_000, 99_000, 95_000])
        for step in range(4):
            destination = account("60701190", eval_day - timedelta(days=random.randint(5, 40)))
            events.append((pix(start + timedelta(minutes=12 * step), amount, customer, destination), SCENARIOS_FRAUD[4], True))

    # Fazenda de aparelhos: um celular operando contas de vários CPFs (contas compradas).
    farm_device = f"dvc-farm-{uuid4().hex[:8]}"
    start = daytime(eval_day)
    for step in range(6):
        laranja = {"account": account(opened=eval_day - timedelta(days=random.randint(30, 300))), "device": farm_device,
                   "ip": "200.10.20.30", "home": {"latitude": -23.5, "longitude": -46.6}}
        destination = account("60701190", eval_day - timedelta(days=10))
        events.append((pix(start + timedelta(minutes=10 * step), random.uniform(20_000, 90_000), laranja, destination),
                       SCENARIOS_FRAUD[5], True))

    # Fracionamento noturno: pedaços abaixo do limite noturno de R$ 1.000,
    # todos para a mesma conta. Cada pedaço sozinho é um Pix normal.
    for customer in customers[len(customers) // 3: len(customers) // 3 + chunk]:
        start = daytime(eval_day, 21, 23)
        destination = account("60701190", eval_day - timedelta(days=random.randint(60, 300)))
        for step in range(3):
            amount = random.randint(95_000, 99_900)
            events.append((pix(start + timedelta(minutes=20 * step), amount, customer, destination), SCENARIOS_FRAUD[6], True))

    return events


def post(payload):
    response = requests.post(f"{API_URL}/pix/transaction", json=payload, headers=HEADERS, timeout=10)
    if response.status_code not in (200, 201):
        raise RuntimeError(f"{response.status_code}: {response.text}")
    return response.json()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--customers", type=int, default=150)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    random.seed(args.seed)

    eval_day = (datetime.now(BRASILIA) - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    customers = build_population(args.customers, eval_day)
    events = generate_history(customers, eval_day) + generate_eval_day(customers, eval_day)
    events.sort(key=lambda event: event[0]["transaction_date"])

    print(f"Enviando {len(events)} Pix para {API_URL} ...")
    outcomes = defaultdict(Counter)
    amounts = defaultdict(Counter)
    rule_counter = Counter()
    latencies = []

    for payload, scenario, measured in events:
        analysis = post(payload)
        if not measured:
            continue
        status = analysis["status"]
        outcomes[scenario][status] += 1
        amounts[scenario][status] += payload["amount"]
        latencies.append(analysis["metadata"]["processing_time_ms"])
        if status != "approved" or scenario != LEGIT:
            for hit in analysis["metadata"]["triggered_rules"]:
                if hit["points"] > 0 or hit["hard"]:
                    rule_counter[(scenario, hit["rule"])] += 1

    print_report(outcomes, amounts, rule_counter, latencies)


def print_report(outcomes, amounts, rule_counter, latencies):
    print()
    print(f"{'cenário':34} {'Pix':>5} {'aprovado':>9} {'2FA':>7} {'rejeitado':>10} {'R$ barrado':>13}")
    for scenario in [LEGIT] + SCENARIOS_FRAUD:
        counts = outcomes[scenario]
        total = sum(counts.values())
        if total == 0:
            continue
        blocked_reais = amounts[scenario]["rejected"] / 100
        print(
            f"{scenario:34} {total:>5} "
            f"{counts['approved'] / total:>9.1%} {counts['two_factor_required'] / total:>7.1%} "
            f"{counts['rejected'] / total:>10.1%} {blocked_reais:>13,.2f}"
        )

    fraud_total = sum(sum(outcomes[s].values()) for s in SCENARIOS_FRAUD)
    fraud_stopped = sum(outcomes[s]["rejected"] + outcomes[s]["two_factor_required"] for s in SCENARIOS_FRAUD)
    legit = outcomes[LEGIT]
    legit_total = sum(legit.values())
    fraud_amount = sum(sum(amounts[s].values()) for s in SCENARIOS_FRAUD)
    fraud_amount_approved = sum(amounts[s]["approved"] for s in SCENARIOS_FRAUD)

    print()
    print(f"Fraudes paradas (2FA ou rejeitado): {fraud_stopped}/{fraud_total} = {fraud_stopped / fraud_total:.1%}")
    print(f"Valor de fraude que passou direto:  R$ {fraud_amount_approved / 100:,.2f} de R$ {fraud_amount / 100:,.2f}")
    print(f"Clientes bons com 2FA:              {legit['two_factor_required'] / legit_total:.1%}")
    print(f"Clientes bons rejeitados:           {legit['rejected'] / legit_total:.1%}")
    latencies.sort()
    print(f"Latência do motor p50 / p95 / p99:  {latencies[len(latencies) // 2]:.1f} / "
          f"{latencies[int(len(latencies) * 0.95)]:.1f} / {latencies[int(len(latencies) * 0.99)]:.1f} ms")

    print()
    print("Regras que mais pesaram em cada cenário:")
    for scenario in [LEGIT] + SCENARIOS_FRAUD:
        top = [(rule, count) for (s, rule), count in rule_counter.most_common() if s == scenario][:4]
        if top:
            print(f"  {scenario}: " + ", ".join(f"{rule} ({count})" for rule, count in top))


if __name__ == "__main__":
    main()
