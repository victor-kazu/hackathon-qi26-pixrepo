"""Simulação de tráfego: clientes bons + ataques, e o que o motor decidiu.

Gera 60 dias de histórico de pessoas e empresas comuns (mercado, amigos,
aluguel, fornecedores, folha) e, no último dia, mistura o uso normal com
casos legítimos fora da curva e doze tipos de fraude. Manda tudo para a
API em ordem cronológica e mede, por cenário, quanto foi aprovado,
desafiado com 2FA ou rejeitado.

"Contido" depende do golpe. Quando quem paga é o fraudador com a conta
de outra pessoa, o 2FA segura (ele não tem o segundo fator). Quando quem
paga é a própria vítima enganada, ou o fraudador é o dono da conta, o
2FA não segura nada: só a rejeição conta.

Os rótulos de fraude são do próprio script: é um teste de consistência
da política contra ataques conhecidos, não uma medida de precisão real
(para isso seria preciso fraude confirmada em produção).

Uso, com a API de pé:

    python scripts/simulate.py                # 150 clientes, semente 42
    python scripts/simulate.py --customers 300 --seed 7
"""

import argparse
import random
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
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

# ISPBs só para as contas de destino parecerem de bancos diferentes.
OTHER_BANK = "60701190"
LANDLORD_BANK = "00360305"
OUR_BANK = "32402502"

CITIES = {
    "São Paulo": (-23.56, -46.65),
    "Rio de Janeiro": (-22.90, -43.20),
    "Belo Horizonte": (-19.92, -43.94),
    "Porto Alegre": (-30.03, -51.23),
    "Recife": (-8.05, -34.88),
}


@dataclass(frozen=True)
class Scenario:
    name: str
    fraud: bool = False
    # Só para fraude: o 2FA impede o golpe? (o fraudador não tem o segundo fator)
    two_factor_stops: bool = False


DAY_TO_DAY = Scenario("dia a dia")
UNDER_ATTACK = Scenario("Pix bom de conta atacada no dia")
RENT = Scenario("aluguel para o mesmo locador")
OWN_ACCOUNT = Scenario("entre contas próprias")
NEW_CUSTOMER = Scenario("cliente novo, Pix comum")
NEW_CUSTOMER_HIGH = Scenario("cliente novo, Pix alto")
BAR_SPLIT = Scenario("racha do bar")
GROUP_COLLECTION = Scenario("vaquinha repassada")
PAYROLL = Scenario("folha de pagamento (empresa)")
SUPPLIER = Scenario("fornecedor (empresa)")

TAKEOVER = Scenario("tomada de conta, aparelho novo", True, True)
STOLEN_PHONE = Scenario("celular roubado à noite", True, True)
NIGHT_DRAIN = Scenario("drenagem noturna espalhada", True, True)
IMPOSSIBLE_TRAVEL = Scenario("viagem impossível", True, True)
BOT = Scenario("robô em rajada", True, True)
STRUCTURING = Scenario("fracionamento, valor repetido", True, True)
NIGHT_SPLIT = Scenario("fracionamento noturno", True, True)
MULE_FAN_IN = Scenario("golpe para conta laranja", True, False)
CHAIN_VICTIM = Scenario("cadeia: vítima paga laranja", True, False)
CHAIN_FORWARD = Scenario("cadeia: laranja repassa", True, False)
CHAIN_HUB = Scenario("cadeia: vítima paga central", True, False)
MIRROR = Scenario("conta espelho (mão fantasma)", True, False)
FAKE_ACCOUNT = Scenario("conta aberta com documento roubado", True, False)
DEVICE_FARM = Scenario("fazenda de aparelhos", True, False)

LEGIT_SCENARIOS = [
    DAY_TO_DAY, UNDER_ATTACK, RENT, OWN_ACCOUNT, NEW_CUSTOMER, NEW_CUSTOMER_HIGH,
    BAR_SPLIT, GROUP_COLLECTION, SUPPLIER, PAYROLL,
]
FRAUD_SCENARIOS = [
    TAKEOVER, STOLEN_PHONE, NIGHT_DRAIN, IMPOSSIBLE_TRAVEL, BOT, STRUCTURING, NIGHT_SPLIT,
    MULE_FAN_IN, CHAIN_VICTIM, CHAIN_FORWARD, CHAIN_HUB, MIRROR, FAKE_ACCOUNT, DEVICE_FARM,
]


def iso(moment):
    return moment.isoformat(timespec="seconds")


def account(participant=None, opened=None, document=None):
    # O número da conta vem do uuid, fora da semente: rodar duas vezes no
    # mesmo banco não mistura o histórico de uma rodada com a outra.
    data = {
        "branch": "0001",
        "account_number": str(10_000_000 + uuid4().int % 90_000_000),
        "opening_date": iso(opened),
        "owner": {"document_number": document or RandomGenerator.generate_cpf()},
    }
    if participant:
        data["participant"] = participant
    return data


def as_destination(source_account):
    """A mesma conta vista como recebedora: o destino precisa do ISPB."""
    return {**source_account, "participant": OUR_BANK}


def person(opened, city=None):
    city = city or random.choice(list(CITIES))
    lat, lon = CITIES[city]
    return {
        "account": account(opened=opened),
        "device": f"dvc-{uuid4().hex[:10]}",
        "ip": f"177.{random.randrange(256)}.{random.randrange(256)}.{random.randrange(1, 255)}",
        "city": city,
        "home": {"latitude": lat + random.uniform(-0.1, 0.1), "longitude": lon + random.uniform(-0.1, 0.1)},
    }


def pix(moment, amount, payer, destination, device=None, geo=True):
    """geo=True manda a localização de casa do pagador; um dict manda aquele lugar; False, nenhum."""
    source = {"device_id": device or payer["device"], "channel": "app", "ip": payer["ip"]}
    location = payer["home"] if geo is True else geo
    if location:
        source["geolocation"] = location
    return {
        "id": f"sim-{uuid4()}",
        "amount": int(amount),
        "transaction_date": iso(moment),
        "source_account": payer["account"],
        "destination_account": destination,
        "source": source,
    }


@dataclass(frozen=True)
class Forward:
    """O repasse do laranja só existe se o dinheiro das vítimas chegou: o valor sai na hora do envio."""

    moment: datetime
    mule: dict
    destination: dict
    inbound: tuple
    share: float

    def build(self, statuses):
        arrived = sum(amount for pix_id, amount in self.inbound if statuses.get(pix_id) in ("approved", "two_factor_required"))
        if arrived == 0:
            return None
        return pix(self.moment, arrived * self.share, self.mule, self.destination)


def when(event):
    item = event[0]
    if isinstance(item, Forward):
        return iso(item.moment)
    return item["transaction_date"]


def at(day, hour_from=8, hour_to=20):
    return day.replace(hour=random.randint(hour_from, hour_to - 1), minute=random.randint(0, 59), second=random.randint(0, 59))


def build_population(customers_count, eval_day):
    start = eval_day - timedelta(days=60)
    customers = []
    for _ in range(customers_count):
        customer = person(opened=start - timedelta(days=random.randint(90, 2000)))
        # Ticket típico do cliente: de R$ 30 a R$ 400.
        customer["ticket"] = random.uniform(3_000, 40_000)
        customer["payees"] = [account(OTHER_BANK, start - timedelta(days=random.randint(200, 3000))) for _ in range(5)]
        customer["landlord"] = account(LANDLORD_BANK, start - timedelta(days=1500))
        customer["rent"] = random.choice([90_000, 150_000, 250_000, 400_000])
        customer["own_account"] = account(
            OTHER_BANK, start - timedelta(days=random.randint(100, 1500)), customer["account"]["owner"]["document_number"]
        )
        customers.append(customer)

    companies = []
    for _ in range(max(3, customers_count // 30)):
        company = person(opened=start - timedelta(days=random.randint(400, 4000)))
        company["account"]["owner"]["document_number"] = RandomGenerator.generate_cnpj()
        company["suppliers"] = [account(OTHER_BANK, start - timedelta(days=random.randint(300, 3000)), RandomGenerator.generate_cnpj())
                                for _ in range(4)]
        company["employees"] = [(account(OTHER_BANK, start - timedelta(days=random.randint(60, 3000))), random.randint(200_000, 600_000))
                                for _ in range(random.randint(8, 15))]
        companies.append(company)

    return customers, companies


def payroll(company, start, scenario):
    """Um Pix por funcionário, um por minuto: o jeito que uma empresa pequena paga pelo app."""
    return [(pix(start + timedelta(minutes=index), salary, company, employee), scenario)
            for index, (employee, salary) in enumerate(company["employees"])]


def generate_history(customers, companies, eval_day):
    events = []
    for days_ago in range(60, 0, -1):
        day = eval_day - timedelta(days=days_ago)
        for customer in customers:
            if random.random() < 0.35:
                amount = customer["ticket"] * random.lognormvariate(0, 0.5)
                events.append((pix(at(day), amount, customer, random.choice(customer["payees"])), None))
            if days_ago in (55, 25):
                events.append((pix(at(day, 9, 12), customer["rent"], customer, customer["landlord"]), None))
        for company in companies:
            if random.random() < 0.3:
                amount = random.uniform(200_000, 2_000_000)
                events.append((pix(at(day, 9, 18), amount, company, random.choice(company["suppliers"])), None))
            if days_ago in (35, 5):
                events.extend((payload, None) for payload, _ in payroll(company, at(day, 9, 11), None))
    return events


class Pool:
    """Distribui os clientes entre os cenários, sem ninguém cair em dois."""

    def __init__(self, customers):
        self.customers = list(customers)
        random.shuffle(self.customers)

    def take(self, count):
        taken, self.customers = self.customers[:count], self.customers[count:]
        return taken


def mark_attacked(customer, moment):
    customer["attacked_at"] = min(moment, customer.get("attacked_at", moment))


def generate_attacks(pool, eval_day, chunk):
    events = []

    # Tomada de conta: o golpista entra de outro aparelho, sem GPS, e manda para destinos novos.
    for customer in pool.take(chunk):
        device, start = f"dvc-atk-{uuid4().hex[:8]}", at(eval_day)
        mark_attacked(customer, start)
        for step in range(3):
            destination = account(OTHER_BANK, eval_day - timedelta(days=random.randint(1, 60)))
            events.append((pix(start + timedelta(minutes=4 * step), random.uniform(50_000, 300_000), customer, destination, device,
                               geo=False), TAKEOVER))

    # Celular roubado e destravado à noite: mesmo aparelho, valor alto, destino novo.
    for customer in pool.take(chunk):
        start = at(eval_day, 21, 24)
        mark_attacked(customer, start)
        for step in range(3):
            destination = account(OTHER_BANK, eval_day - timedelta(days=random.randint(1, 20)))
            events.append((pix(start + timedelta(minutes=3 * step), random.uniform(80_000, 400_000), customer, destination),
                           STOLEN_PHONE))

    # Drenagem de madrugada: R$ 900 a R$ 990 para contas antigas diferentes, nenhum valor repetido.
    for customer in pool.take(chunk):
        start = at(eval_day, 0, 3)
        mark_attacked(customer, start)
        for step, amount in enumerate(random.sample(range(90_000, 99_000, 500), 6)):
            destination = account(OTHER_BANK, eval_day - timedelta(days=random.randint(60, 900)))
            events.append((pix(start + timedelta(minutes=7 * step), amount, customer, destination), NIGHT_DRAIN))

    # Viagem impossível: o cliente paga em casa e, minutos depois, alguém em outra capital usa a conta.
    for customer in pool.take(chunk):
        start = at(eval_day, 9, 18)
        events.append((pix(start, customer["ticket"], customer, random.choice(customer["payees"])), DAY_TO_DAY))
        far_city = random.choice([city for city in CITIES if city != customer["city"]])
        lat, lon = CITIES[far_city]
        device = f"dvc-atk-{uuid4().hex[:8]}"
        attack_start = start + timedelta(minutes=random.randint(10, 20))
        mark_attacked(customer, attack_start)
        for step in range(2):
            destination = account(OTHER_BANK, eval_day - timedelta(days=random.randint(5, 200)))
            events.append((pix(attack_start + timedelta(minutes=5 * step), random.uniform(30_000, 200_000), customer, destination,
                               device, geo={"latitude": lat, "longitude": lon}), IMPOSSIBLE_TRAVEL))

    # Robô: Pix pequenos em sequência para esvaziar a conta.
    for customer in pool.take(chunk):
        start = at(eval_day)
        mark_attacked(customer, start)
        destination = account(OTHER_BANK, eval_day - timedelta(days=30))
        for step in range(8):
            events.append((pix(start + timedelta(seconds=20 * step), random.uniform(5_000, 20_000), customer, destination), BOT))

    # Fracionamento: o mesmo valor várias vezes, destinos diferentes, abaixo dos limites.
    for customer in pool.take(chunk):
        start = at(eval_day)
        mark_attacked(customer, start)
        amount = random.choice([49_000, 99_000, 95_000])
        for step in range(4):
            destination = account(OTHER_BANK, eval_day - timedelta(days=random.randint(5, 40)))
            events.append((pix(start + timedelta(minutes=12 * step), amount, customer, destination), STRUCTURING))

    # Fracionamento noturno: pedaços abaixo do limite noturno de R$ 1.000, todos para a mesma conta.
    for customer in pool.take(chunk):
        start = at(eval_day, 21, 23)
        mark_attacked(customer, start)
        destination = account(OTHER_BANK, eval_day - timedelta(days=random.randint(60, 300)))
        for step in range(3):
            events.append((pix(start + timedelta(minutes=20 * step), random.randint(95_000, 99_900), customer, destination),
                           NIGHT_SPLIT))

    # Engenharia social: vítimas, no próprio aparelho, pagando 3 contas laranja recém-abertas.
    mules = [account(OTHER_BANK, eval_day - timedelta(days=random.randint(1, 5))) for _ in range(3)]
    for index, customer in enumerate(pool.take(2 * chunk)):
        moment = eval_day.replace(hour=14) + timedelta(minutes=7 * index)
        events.append((pix(moment, customer["ticket"] * random.uniform(3, 15), customer, mules[index % 3]), MULE_FAN_IN))

    events.extend(generate_mule_chain(pool, eval_day, chunk))

    # Conta espelho: por acesso remoto, o golpista esvazia a conta da vítima para uma conta
    # aberta anteontem, em outro banco, no CPF dela. Quem confirma o 2FA é a própria vítima.
    for customer in pool.take(chunk):
        document = customer["account"]["owner"]["document_number"]
        mirror = account(OTHER_BANK, eval_day - timedelta(days=random.randint(1, 3)), document)
        events.append((pix(at(eval_day), random.uniform(300_000, 1_500_000), customer, mirror), MIRROR))

    # Conta aberta com documento roubado: sem histórico, já sai mandando valor alto.
    for _ in range(chunk):
        fraudster = person(opened=eval_day - timedelta(days=random.randint(0, 2), hours=random.randint(1, 20)))
        start = at(eval_day, 10, 18)
        for step in range(2):
            destination = account(OTHER_BANK, eval_day - timedelta(days=random.randint(1, 10)))
            events.append((pix(start + timedelta(minutes=25 * step), random.uniform(200_000, 800_000), fraudster, destination),
                           FAKE_ACCOUNT))

    # Fazenda de aparelhos: um celular operando contas de vários CPFs (contas compradas).
    farm_device, start = f"dvc-farm-{uuid4().hex[:8]}", at(eval_day)
    for step in range(6):
        bought = person(opened=eval_day - timedelta(days=random.randint(30, 300)), city="São Paulo")
        bought["ip"] = "200.10.20.30"
        destination = account(OTHER_BANK, eval_day - timedelta(days=10))
        events.append((pix(start + timedelta(minutes=10 * step), random.uniform(20_000, 90_000), bought, destination, farm_device),
                       DEVICE_FARM))

    return events


def generate_mule_chain(pool, eval_day, chunk):
    """Vítima -> laranja -> conta central, e depois novas vítimas pagando a central direto."""
    events = []
    hub = account(OTHER_BANK, eval_day - timedelta(days=random.randint(15, 40)))
    victims = pool.take(3 * chunk)
    mules = [person(opened=eval_day - timedelta(days=random.randint(2, 6))) for _ in range(max(2, chunk))]

    victims_of_mules, hub_victims = victims[: 2 * len(mules)], victims[2 * len(mules):]
    for index, mule in enumerate(mules):
        start = eval_day.replace(hour=10) + timedelta(minutes=45 * index)
        inbound = []
        for step, victim in enumerate(victims_of_mules[2 * index: 2 * index + 2]):
            # O golpista pede um valor que a vítima consegue mandar: de 1,5 a 3 vezes o ticket dela, no mínimo R$ 600.
            amount = max(60_000, victim["ticket"] * random.uniform(1.5, 3))
            payload = pix(start + timedelta(minutes=10 * step), amount, victim, as_destination(mule["account"]))
            inbound.append((payload["id"], payload["amount"]))
            events.append((payload, CHAIN_VICTIM))
        forward_at = start + timedelta(minutes=random.randint(40, 90))
        events.append((Forward(forward_at, mule, hub, tuple(inbound), random.uniform(0.9, 0.95)), CHAIN_FORWARD))

    for index, victim in enumerate(hub_victims):
        moment = eval_day.replace(hour=16) + timedelta(minutes=12 * index)
        events.append((pix(moment, victim["ticket"] * random.uniform(3, 10), victim, hub), CHAIN_HUB))

    return events


def legit(customer, moment, scenario):
    """Pix bom de quem foi atacado no dia sai do placar: o motor tem motivo para desconfiar dele."""
    attacked_at = customer.get("attacked_at")
    if attacked_at and moment >= attacked_at:
        return UNDER_ATTACK
    return scenario


def generate_good_day(customers, pool, companies, eval_day, chunk):
    """O dia dos clientes bons, com os casos legítimos que mais se parecem com fraude."""
    events = []

    # Vaquinha: quatro a seis amigos pagam o organizador, que repassa quase tudo para a pousada.
    for organizer in pool.take(chunk):
        friends = random.sample([customer for customer in customers if customer is not organizer], random.randint(4, 6))
        start, received = at(eval_day, 9, 12), 0
        for step, friend in enumerate(friends):
            amount, moment = random.randint(150, 400) * 100, start + timedelta(minutes=17 * step)
            received = received + amount
            events.append((pix(moment, amount, friend, as_destination(organizer["account"])), legit(friend, moment, DAY_TO_DAY)))
        venue = account(OTHER_BANK, eval_day - timedelta(days=random.randint(300, 3000)))
        moment = at(eval_day, 15, 18)
        events.append((pix(moment, received * random.uniform(0.9, 1.0), organizer, venue), legit(organizer, moment, GROUP_COLLECTION)))

    # Racha do bar: quem pagou a conta recebe a parte de cada amigo, todos o mesmo valor, em minutos.
    for host in pool.take(chunk):
        friends = random.sample([customer for customer in customers if customer is not host], random.randint(4, 6))
        start, share = at(eval_day, 21, 23), random.randint(40, 120) * 100
        for step, friend in enumerate(friends):
            moment = start + timedelta(minutes=2 * step)
            events.append((pix(moment, share, friend, as_destination(host["account"])), legit(friend, moment, BAR_SPLIT)))

    for customer in customers:
        for _ in range(random.randint(1, 3)):
            roll = random.random()
            if roll < 0.6:
                destination, moment = random.choice(customer["payees"]), at(eval_day)
                amount = customer["ticket"] * random.lognormvariate(0, 0.5)
            elif roll < 0.85:
                destination, moment = account(OTHER_BANK, eval_day - timedelta(days=random.randint(10, 3000))), at(eval_day)
                amount = customer["ticket"] * random.lognormvariate(0.3, 0.6)
            else:
                destination, moment = random.choice(customer["payees"]), at(eval_day, 20, 24)
                amount = customer["ticket"] * random.lognormvariate(0, 0.5)
            events.append((pix(moment, amount, customer, destination), legit(customer, moment, DAY_TO_DAY)))
        if random.random() < 0.3:
            moment = at(eval_day, 9, 12)
            events.append((pix(moment, customer["rent"], customer, customer["landlord"]), legit(customer, moment, RENT)))
        if random.random() < 0.1:
            moment = at(eval_day)
            events.append((pix(moment, random.uniform(100_000, 1_000_000), customer, customer["own_account"]),
                           legit(customer, moment, OWN_ACCOUNT)))

    # Cliente novo: conta aberta há semanas ou meses, nenhum Pix ainda.
    for _ in range(len(customers) // 10):
        newcomer = person(opened=eval_day - timedelta(days=random.randint(20, 400)))
        destination = account(OTHER_BANK, eval_day - timedelta(days=random.randint(100, 3000)))
        if random.random() < 0.6:
            events.append((pix(at(eval_day), random.uniform(5_000, 50_000), newcomer, destination), NEW_CUSTOMER))
        else:
            # Entrada de um carro usado, sinal de um apartamento.
            events.append((pix(at(eval_day), random.uniform(500_000, 1_200_000), newcomer, destination), NEW_CUSTOMER_HIGH))

    for company in companies:
        for _ in range(random.randint(0, 2)):
            amount = random.uniform(200_000, 2_000_000)
            events.append((pix(at(eval_day, 9, 18), amount, company, random.choice(company["suppliers"])), SUPPLIER))
        events.extend(payroll(company, at(eval_day, 9, 11), PAYROLL))

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
    customers, companies = build_population(args.customers, eval_day)
    history = generate_history(customers, companies, eval_day)

    pool = Pool(customers)
    chunk = max(2, args.customers // 25)
    attacks = generate_attacks(pool, eval_day, chunk)
    good_day = generate_good_day(customers, pool, companies, eval_day, chunk)

    events = history + attacks + good_day
    events.sort(key=when)

    print(f"Enviando até {len(events)} Pix para {API_URL} ({len(history)} de histórico) ...")
    results, statuses = [], {}
    for index, (item, scenario) in enumerate(events, start=1):
        payload = item.build(statuses) if isinstance(item, Forward) else item
        if payload is None:
            continue
        analysis = post(payload)
        statuses[payload["id"]] = analysis["status"]
        if scenario is not None:
            results.append((scenario, payload["amount"], analysis))
        if index % 1000 == 0:
            print(f"  {index}/{len(events)}")

    print_report(results)


def contained(scenario, status):
    if status == "rejected":
        return True
    return status == "two_factor_required" and scenario.two_factor_stops


def print_report(results):
    by_scenario = defaultdict(list)
    for scenario, amount, analysis in results:
        by_scenario[scenario].append((amount, analysis))

    def shares(rows):
        statuses = Counter(analysis["status"] for _, analysis in rows)
        return [statuses[status] / len(rows) for status in ("approved", "two_factor_required", "rejected")]

    print()
    print("CLIENTES BONS")
    print(f"{'cenário':36} {'Pix':>5} {'aprovado':>9} {'2FA':>7} {'rejeitado':>10}")
    for scenario in LEGIT_SCENARIOS:
        rows = by_scenario.get(scenario)
        if rows:
            approved, two_factor, rejected = shares(rows)
            print(f"{scenario.name:36} {len(rows):>5} {approved:>9.1%} {two_factor:>7.1%} {rejected:>10.1%}")

    print()
    print("FRAUDES  (2FA segura? = o 2FA impede este golpe)")
    print(f"{'cenário':36} {'Pix':>5} {'aprovado':>9} {'2FA':>7} {'rejeitado':>10} {'2FA segura?':>12} {'contido':>8} "
          f"{'R$ tentado':>13} {'R$ escapou':>12}")
    for scenario in FRAUD_SCENARIOS:
        rows = by_scenario.get(scenario)
        if not rows:
            continue
        approved, two_factor, rejected = shares(rows)
        stopped = sum(1 for _, analysis in rows if contained(scenario, analysis["status"])) / len(rows)
        attempted = sum(amount for amount, _ in rows)
        escaped = sum(amount for amount, analysis in rows if not contained(scenario, analysis["status"]))
        print(f"{scenario.name:36} {len(rows):>5} {approved:>9.1%} {two_factor:>7.1%} {rejected:>10.1%} "
              f"{'sim' if scenario.two_factor_stops else 'não':>12} {stopped:>8.1%} {attempted / 100:>13,.2f} {escaped / 100:>12,.2f}")

    fraud_rows = [(scenario, amount, analysis) for scenario, amount, analysis in results if scenario.fraud]
    legit_rows = [(scenario, amount, analysis) for scenario, amount, analysis in results if not scenario.fraud]
    clean_rows = [row for row in legit_rows if row[0] is not UNDER_ATTACK]
    fraud_stopped = [row for row in fraud_rows if contained(row[0], row[2]["status"])]
    fraud_amount = sum(amount for _, amount, _ in fraud_rows)
    stopped_amount = sum(amount for _, amount, _ in fraud_stopped)

    def rate(rows, status):
        return sum(1 for row in rows if row[2]["status"] == status) / len(rows)

    print()
    print(f"Fraude contida, por Pix:           {len(fraud_stopped)}/{len(fraud_rows)} = {len(fraud_stopped) / len(fraud_rows):.1%}")
    print(f"Fraude contida, por valor:         R$ {stopped_amount / 100:,.2f} de R$ {fraud_amount / 100:,.2f} "
          f"= {stopped_amount / fraud_amount:.1%}")
    print(f"Clientes bons aprovados direto:    {rate(clean_rows, 'approved'):.1%} "
          f"(2FA {rate(clean_rows, 'two_factor_required'):.1%}, rejeitados {rate(clean_rows, 'rejected'):.1%}; "
          f"{len(clean_rows)} Pix, sem contar contas sob ataque)")

    latencies = sorted(analysis["metadata"]["processing_time_ms"] for _, _, analysis in results)
    print(f"Latência do motor p50 / p95 / p99: {latencies[len(latencies) // 2]:.1f} / "
          f"{latencies[int(len(latencies) * 0.95)]:.1f} / {latencies[int(len(latencies) * 0.99)]:.1f} ms")

    print()
    print("Por que o motor agiu (regras com peso nos Pix que não foram aprovados direto, ou em toda fraude):")
    for scenario in LEGIT_SCENARIOS + FRAUD_SCENARIOS:
        rules = Counter()
        for _, analysis in by_scenario.get(scenario, []):
            if analysis["status"] == "approved" and not scenario.fraud:
                continue
            for hit in analysis["metadata"]["triggered_rules"]:
                if hit["points"] > 0 or hit["hard"]:
                    rules[hit["rule"]] += 1
        if rules:
            print(f"  {scenario.name}: " + ", ".join(f"{rule} ({count})" for rule, count in rules.most_common(4)))

    rejected_good = Counter(
        " + ".join(hit["rule"] for hit in analysis["metadata"]["triggered_rules"] if hit["points"] > 0 or hit["hard"])
        for scenario, _, analysis in clean_rows
        if analysis["status"] == "rejected"
    )
    if rejected_good:
        print()
        print("Clientes bons rejeitados, por combinação de regras:")
        for combination, count in rejected_good.most_common():
            print(f"  {count:>3}  {combination}")


if __name__ == "__main__":
    main()
