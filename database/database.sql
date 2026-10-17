-- Motor antifraude Pix: o histórico de tudo que chegou e de cada decisão.
--
-- Datas são sempre TIMESTAMPTZ: o Pix chega com fuso ("-03:00") e as
-- janelas de velocity comparam instantes, não horários de parede.

CREATE TABLE pix_analysis_status(
    id                              SERIAL PRIMARY KEY,
    enumerator                      VARCHAR(30) NOT NULL,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(enumerator)
);

INSERT INTO pix_analysis_status (enumerator) VALUES
('approved'),
('two_factor_required'),
('rejected');

-- Um Pix recebido, com os campos do contrato em colunas tipadas (é por
-- elas que as regras consultam o histórico) e o corpo original em JSONB
-- para auditoria.
CREATE TABLE pix_transaction(
    id                              BIGSERIAL PRIMARY KEY,
    pix_id                          VARCHAR(100) NOT NULL,
    amount                          BIGINT NOT NULL CHECK (amount > 0),
    transaction_date                TIMESTAMPTZ NOT NULL,
    -- O fuso de quem pagou. O TIMESTAMPTZ guarda o instante em UTC e
    -- perde o fuso; sem esta coluna não daria pra saber se foi de noite
    -- na hora local do cliente.
    utc_offset_minutes              SMALLINT NOT NULL,

    source_branch                   VARCHAR(10) NOT NULL,
    source_account_number           VARCHAR(20) NOT NULL,
    source_opening_date             TIMESTAMPTZ NOT NULL,
    source_document_number          VARCHAR(14) NOT NULL,

    destination_participant         CHAR(8) NOT NULL,
    destination_branch              VARCHAR(10) NOT NULL,
    destination_account_number      VARCHAR(20) NOT NULL,
    destination_opening_date        TIMESTAMPTZ NOT NULL,
    destination_document_number     VARCHAR(14) NOT NULL,

    device_id                       VARCHAR(100) NOT NULL,
    channel                         VARCHAR(30) NOT NULL,
    ip                              VARCHAR(45) NOT NULL,
    latitude                        NUMERIC(9, 6),
    longitude                       NUMERIC(9, 6),

    raw_payload                     JSONB NOT NULL,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(pix_id)
);

-- Cada regra de histórico é uma consulta "deste pagador / deste destino
-- / deste aparelho, numa janela de tempo". Os índices seguem exatamente
-- essas perguntas, com a data por último para o intervalo virar range
-- scan em vez de varrer a tabela.
CREATE INDEX idx_pix_transaction_source_date
    ON pix_transaction (source_branch, source_account_number, transaction_date);
CREATE INDEX idx_pix_transaction_destination_date
    ON pix_transaction (destination_participant, destination_branch, destination_account_number, transaction_date);
CREATE INDEX idx_pix_transaction_device_date
    ON pix_transaction (device_id, transaction_date);
CREATE INDEX idx_pix_transaction_ip_date
    ON pix_transaction (ip, transaction_date);

-- A decisão sobre um Pix. 1:1 com a transação, mas separada dela: a
-- transação é o fato que chegou, a análise é o que o motor concluiu
-- (e com qual versão da política).
CREATE TABLE pix_analysis(
    id                              BIGSERIAL PRIMARY KEY,
    analysis_key                    CHAR(36) NOT NULL,
    pix_transaction_id              BIGINT NOT NULL REFERENCES pix_transaction(id),
    status_id                       INTEGER NOT NULL REFERENCES pix_analysis_status(id),
    risk_score                      INTEGER NOT NULL,
    identity_score                  INTEGER NOT NULL,
    beneficiary_score               INTEGER NOT NULL,
    decision_reason                 TEXT NOT NULL,
    policy_version                  VARCHAR(30) NOT NULL,
    processing_time_ms              NUMERIC(10, 3) NOT NULL,
    analysis_metadata               JSONB NOT NULL,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(analysis_key),
    UNIQUE(pix_transaction_id)
);

CREATE INDEX idx_pix_analysis_status ON pix_analysis (status_id);

-- Uma linha por regra acionada. É o que permite perguntar ao banco
-- "qual regra mais manda cliente pro 2FA?" sem abrir JSON nenhum.
CREATE TABLE pix_analysis_rule_hit(
    id                              BIGSERIAL PRIMARY KEY,
    pix_analysis_id                 BIGINT NOT NULL REFERENCES pix_analysis(id),
    rule_code                       VARCHAR(50) NOT NULL,
    category                        VARCHAR(20) NOT NULL,
    points                          INTEGER NOT NULL,
    is_hard_rule                    BOOLEAN NOT NULL,
    detail                          TEXT NOT NULL,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT(NOW())
);

CREATE INDEX idx_pix_analysis_rule_hit_analysis ON pix_analysis_rule_hit (pix_analysis_id);
CREATE INDEX idx_pix_analysis_rule_hit_code ON pix_analysis_rule_hit (rule_code);
