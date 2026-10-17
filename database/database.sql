-- Motor antifraude Pix: tudo o que chega fica gravado, e a analise
-- de cada Pix guarda o motivo de cada regra que disparou.
--
-- Este arquivo e so ASCII de proposito: o DbUtils dos testes le o .sql
-- com a codificacao padrao do sistema, e no Windows ela nao e UTF-8.

-- Um pagador por documento (CPF ou CNPJ). A linha existe para ser
-- TRAVADA: duas analises do mesmo pagador nao podem ler o historico ao
-- mesmo tempo, senao as duas contam "4 Pix em 10 minutos" e passam.
CREATE TABLE payer(
    id                              SERIAL PRIMARY KEY,
    document_number                 VARCHAR(18) NOT NULL,
    document_type                   VARCHAR(4) NOT NULL CHECK (document_type IN ('CPF', 'CNPJ')),
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(document_number)
);

-- O Pix como chegou. As colunas sao planas (e nao um JSON) porque e
-- sobre elas que o historico e consultado: velocidade por pagador,
-- pagadores por recebedor, pagadores por aparelho.
CREATE TABLE pix_transaction(
    id                              SERIAL PRIMARY KEY,
    transaction_id                  VARCHAR(100) NOT NULL,
    payload_hash                    CHAR(64) NOT NULL,
    payer_id                        INTEGER NOT NULL REFERENCES payer(id),
    amount                          BIGINT NOT NULL CHECK (amount > 0),
    transaction_date                TIMESTAMPTZ NOT NULL,
    utc_offset_minutes              SMALLINT NOT NULL,

    source_branch                   VARCHAR(10) NOT NULL,
    source_account_number           VARCHAR(20) NOT NULL,
    source_opening_date             TIMESTAMPTZ NOT NULL,
    source_document_number          VARCHAR(18) NOT NULL,

    destination_participant         CHAR(8) NOT NULL,
    destination_branch              VARCHAR(10) NOT NULL,
    destination_account_number      VARCHAR(20) NOT NULL,
    destination_opening_date        TIMESTAMPTZ NOT NULL,
    destination_document_number     VARCHAR(18) NOT NULL,
    destination_document_type       VARCHAR(4) NOT NULL CHECK (destination_document_type IN ('CPF', 'CNPJ')),

    device_id                       VARCHAR(100) NOT NULL,
    channel                         VARCHAR(30) NOT NULL,
    ip                              VARCHAR(45) NOT NULL,
    latitude                        NUMERIC(9,6),
    longitude                       NUMERIC(9,6),

    received_at                     TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(transaction_id),
    CHECK ((latitude IS NULL) = (longitude IS NULL))
);

-- Cada consulta de historico do motor tem o seu indice.
CREATE INDEX pix_transaction_payer_date ON pix_transaction(payer_id, transaction_date);
CREATE INDEX pix_transaction_destination_date
    ON pix_transaction(destination_participant, destination_branch, destination_account_number, transaction_date);
CREATE INDEX pix_transaction_device_date ON pix_transaction(device_id, transaction_date);

-- A decisao. Uma por Pix: o UNIQUE(pix_transaction_id) e o que impede
-- um reenvio de gerar uma segunda analise.
CREATE TABLE pix_analysis(
    id                              SERIAL PRIMARY KEY,
    analysis_key                    UUID NOT NULL,
    pix_transaction_id              INTEGER NOT NULL REFERENCES pix_transaction(id),
    status                          VARCHAR(20) NOT NULL
                                        CHECK (status IN ('approved', 'two_factor_required', 'rejected')),
    score                           INTEGER NOT NULL CHECK (score >= 0),
    decided_by                      VARCHAR(50) NOT NULL,
    rules_version                   VARCHAR(20) NOT NULL,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(analysis_key),
    UNIQUE(pix_transaction_id)
);

-- Cada regra que disparou, com o dado que a fez disparar. E daqui que
-- sai o "metadata" da resposta, e e o que torna a decisao reconstruivel.
CREATE TABLE pix_rule_hit(
    id                              SERIAL PRIMARY KEY,
    pix_analysis_id                 INTEGER NOT NULL REFERENCES pix_analysis(id),
    rule_code                       VARCHAR(50) NOT NULL,
    points                          INTEGER NOT NULL,
    forced_status                   VARCHAR(20)
                                        CHECK (forced_status IN ('two_factor_required', 'rejected')),
    detail                          JSONB NOT NULL,
    UNIQUE(pix_analysis_id, rule_code)
);
