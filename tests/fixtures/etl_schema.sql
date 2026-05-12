-- Schema fixture mirroring /home/dwk/code/usKoreaJob/etl/load/loader.py
-- Used by integration tests so they exercise the exact shape the API
-- reads in production. Keep in sync if the ETL loader changes.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS job_postings (
    id                 BIGSERIAL PRIMARY KEY,
    record_id          VARCHAR(64) UNIQUE NOT NULL,
    source             VARCHAR(32) NOT NULL,
    title              TEXT,
    company            TEXT,
    company_inferred   BOOLEAN DEFAULT FALSE,
    location           JSONB,
    salary             JSONB,
    description        TEXT,
    description_length INTEGER,
    job_category       JSONB,
    language           VARCHAR(20),
    post_date          DATE,
    post_date_raw      TEXT,
    link               TEXT,
    contact            TEXT,
    scraped_at         TIMESTAMPTZ NOT NULL,
    meta               JSONB,
    created_at         TIMESTAMPTZ DEFAULT NOW(),
    updated_at         TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_jp_source         ON job_postings(source);
CREATE INDEX IF NOT EXISTS idx_jp_post_date      ON job_postings(post_date);
CREATE INDEX IF NOT EXISTS idx_jp_language       ON job_postings(language);
CREATE INDEX IF NOT EXISTS idx_jp_company        ON job_postings(company);
CREATE INDEX IF NOT EXISTS idx_jp_location_city  ON job_postings((location->>'city'));
CREATE INDEX IF NOT EXISTS idx_jp_location_state ON job_postings((location->>'state'));
CREATE INDEX IF NOT EXISTS idx_jp_salary_min     ON job_postings((salary->>'min'));
CREATE INDEX IF NOT EXISTS idx_jp_salary_max     ON job_postings((salary->>'max'));
CREATE INDEX IF NOT EXISTS idx_jp_salary_unit    ON job_postings((salary->>'unit'));
CREATE INDEX IF NOT EXISTS idx_jp_location_gin   ON job_postings USING GIN(location);
CREATE INDEX IF NOT EXISTS idx_jp_salary_gin     ON job_postings USING GIN(salary);
CREATE INDEX IF NOT EXISTS idx_jp_category_gin   ON job_postings USING GIN(job_category);
CREATE INDEX IF NOT EXISTS idx_jp_title_trgm     ON job_postings USING GIN(title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_jp_description_trgm ON job_postings USING GIN(description gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_jp_company_trgm   ON job_postings USING GIN(company gin_trgm_ops);
