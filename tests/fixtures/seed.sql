-- Dev seed for docker-compose.dev.yml.
--
-- Mounted into the Postgres container at
-- /docker-entrypoint-initdb.d/seed.sql so it runs automatically on the
-- very first start (when the pg_data volume is empty). Subsequent
-- starts reuse the existing volume and skip this file.
--
-- Contents:
--   1. ETL-owned DDL (mirrors /home/dwk/code/usKoreaJob/etl/load/loader.py).
--      apiV2 never creates these tables in production — they are owned by
--      the ETL pipeline — but dev environments need them to exist somewhere.
--   2. 25 realistic rows covering every source, language, salary shape,
--      and a representative slice of the 16 job categories. Used to
--      smoke-test the API and to drive the Meilisearch reindex.
--
-- Safe to re-run on an empty DB; idempotent via IF NOT EXISTS / ON CONFLICT.

BEGIN;

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

CREATE INDEX IF NOT EXISTS idx_jp_source           ON job_postings(source);
CREATE INDEX IF NOT EXISTS idx_jp_post_date        ON job_postings(post_date);
CREATE INDEX IF NOT EXISTS idx_jp_language         ON job_postings(language);
CREATE INDEX IF NOT EXISTS idx_jp_company          ON job_postings(company);
CREATE INDEX IF NOT EXISTS idx_jp_location_city    ON job_postings((location->>'city'));
CREATE INDEX IF NOT EXISTS idx_jp_location_state   ON job_postings((location->>'state'));
CREATE INDEX IF NOT EXISTS idx_jp_salary_min       ON job_postings((salary->>'min'));
CREATE INDEX IF NOT EXISTS idx_jp_salary_max       ON job_postings((salary->>'max'));
CREATE INDEX IF NOT EXISTS idx_jp_salary_unit      ON job_postings((salary->>'unit'));
CREATE INDEX IF NOT EXISTS idx_jp_location_gin     ON job_postings USING GIN(location);
CREATE INDEX IF NOT EXISTS idx_jp_salary_gin       ON job_postings USING GIN(salary);
CREATE INDEX IF NOT EXISTS idx_jp_category_gin     ON job_postings USING GIN(job_category);
CREATE INDEX IF NOT EXISTS idx_jp_title_trgm       ON job_postings USING GIN(title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_jp_description_trgm ON job_postings USING GIN(description gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_jp_company_trgm     ON job_postings USING GIN(company gin_trgm_ops);

-- ────────────────────────────────── Seed data ──────────────────────────────────

INSERT INTO job_postings
    (record_id, source, title, company, company_inferred, location, salary,
     description, description_length, job_category, language, post_date,
     scraped_at, meta)
VALUES
-- 1. gtksa / bilingual / yearly 55k / GA
('seed-001', 'gtksa',
 '현대/기아글로비스 사업장 내 근무, 물류/품질관리 신입 채용',
 '아이씨엔그룹', false,
 '{"raw": "West Point, GA", "city": "West Point", "state": "GA"}'::jsonb,
 '{"min": 55000.0, "max": 55000.0, "unit": "yearly", "currency": "USD", "parsed": true, "raw": "$55K/year"}'::jsonb,
 'Logistics coordination role. 물류 관리 및 품질관리 신입 환영.', 40,
 '["office", "warehouse", "manufacturing"]'::jsonb,
 'bilingual', '2026-05-12', '2026-05-12 11:00:00+00',
 '{"record_id": "seed-001", "schema_version": "1.0"}'::jsonb),

-- 2. gtksa / korean / null salary / AL
('seed-002', 'gtksa',
 '앨라배마주 Ford 1차 협력사 Production Team Manager 채용',
 '아이씨엔그룹', false,
 '{"raw": "AL", "city": null, "state": "AL"}'::jsonb,
 '{"min": null, "max": null, "unit": null, "currency": null, "parsed": false, "raw": null}'::jsonb,
 '제조업 관리 포지션. 경력자 우대.', 22,
 '["manufacturing", "office"]'::jsonb,
 'korean', null, '2026-05-12 09:30:00+00',
 '{"record_id": "seed-002", "schema_version": "1.0"}'::jsonb),

-- 3. gtksa / korean / yearly 60k / TX
('seed-003', 'gtksa',
 '텍사스 한인 제조업체 엔지니어 모집',
 '태평양파트너스', false,
 '{"raw": "Austin, TX", "city": "Austin", "state": "TX"}'::jsonb,
 '{"min": 60000.0, "max": 65000.0, "unit": "yearly", "currency": "USD", "parsed": true, "raw": "$60-65K"}'::jsonb,
 'CAD 경험자 우대. 정규직 채용.', 19,
 '["manufacturing", "office"]'::jsonb,
 'korean', '2026-05-10', '2026-05-10 14:20:00+00',
 '{"record_id": "seed-003", "schema_version": "1.0"}'::jsonb),

-- 4. indeed / english / yearly 75-100k / CA
('seed-004', 'indeed',
 'Pharmacy Account Executive',
 '986 Pharmacy', false,
 '{"raw": "San Marino, CA 91108", "city": "San Marino", "state": "CA"}'::jsonb,
 '{"min": 75000.0, "max": 100000.0, "unit": "yearly", "currency": "USD", "parsed": true, "raw": "$75k-$100k"}'::jsonb,
 'Sell pharmacy services to healthcare providers in SoCal.', 55,
 '["retail", "healthcare"]'::jsonb,
 'english', '2026-05-01', '2026-05-11 12:00:00+00',
 '{"record_id": "seed-004", "schema_version": "1.0"}'::jsonb),

-- 5. indeed / english / yearly 90-120k / CA
('seed-005', 'indeed',
 'Software Engineer (Healthcare Platform)',
 'HealthTech Inc', false,
 '{"raw": "San Francisco, CA", "city": "San Francisco", "state": "CA"}'::jsonb,
 '{"min": 90000.0, "max": 120000.0, "unit": "yearly", "currency": "USD", "parsed": true, "raw": "$90k-$120k"}'::jsonb,
 'Build data pipelines for a healthcare analytics platform.', 58,
 '["healthcare", "office"]'::jsonb,
 'english', '2026-05-05', '2026-05-11 16:45:00+00',
 '{"record_id": "seed-005", "schema_version": "1.0"}'::jsonb),

-- 6. linkedin / english / hourly 20-25 / WA
('seed-006', 'linkedin',
 'Warehouse Associate',
 'LinkedIn Warehouse Inc', true,
 '{"raw": "Seattle, WA", "city": "Seattle", "state": "WA"}'::jsonb,
 '{"min": 20.0, "max": 25.0, "unit": "hourly", "currency": "USD", "parsed": true, "raw": "$20-25/hr"}'::jsonb,
 'Sort packages on second shift. Overtime available.', 48,
 '["warehouse"]'::jsonb,
 'english', '2026-05-10', '2026-05-10 08:00:00+00',
 '{"record_id": "seed-006", "schema_version": "1.0"}'::jsonb),

-- 7. linkedin / english / yearly 80-110k / NY
('seed-007', 'linkedin',
 'Logistics Manager',
 'EastCoast Logistics', false,
 '{"raw": "New York, NY", "city": "New York", "state": "NY"}'::jsonb,
 '{"min": 80000.0, "max": 110000.0, "unit": "yearly", "currency": "USD", "parsed": true, "raw": "$80-110k"}'::jsonb,
 'Oversee last-mile logistics across the NYC metro.', 48,
 '["warehouse", "office"]'::jsonb,
 'english', '2026-05-08', '2026-05-08 10:15:00+00',
 '{"record_id": "seed-007", "schema_version": "1.0"}'::jsonb),

-- 8. jobkoreausa / korean / monthly 4000 / NY
('seed-008', 'jobkoreausa',
 '뉴욕 맨해튼 한인 식당 매니저 구인',
 '서울갈비', false,
 '{"raw": "New York, NY", "city": "New York", "state": "NY"}'::jsonb,
 '{"min": 4000.0, "max": 4500.0, "unit": "monthly", "currency": "USD", "parsed": true, "raw": "월 $4000-4500"}'::jsonb,
 '주 6일 근무, 팁 별도. 경력자 환영.', 22,
 '["restaurant"]'::jsonb,
 'korean', '2026-05-09', '2026-05-09 11:40:00+00',
 '{"record_id": "seed-008", "schema_version": "1.0"}'::jsonb),

-- 9. jobkoreausa / korean / yearly 45-55k / NJ
('seed-009', 'jobkoreausa',
 '뉴저지 한인 제조업 사무직 모집',
 'KNJ 트레이딩', false,
 '{"raw": "Fort Lee, NJ", "city": "Fort Lee", "state": "NJ"}'::jsonb,
 '{"min": 45000.0, "max": 55000.0, "unit": "yearly", "currency": "USD", "parsed": true, "raw": "$45-55K"}'::jsonb,
 '오피스 매니저 겸 회계 보조. 엑셀 능통자.', 28,
 '["office", "manufacturing"]'::jsonb,
 'korean', '2026-05-07', '2026-05-07 13:00:00+00',
 '{"record_id": "seed-009", "schema_version": "1.0"}'::jsonb),

-- 10. jobkoreausa / bilingual / hourly 18-22 / NJ
('seed-010', 'jobkoreausa',
 '뉴저지 리테일 매장 매니저 (bilingual)',
 'K-Mart Retail', false,
 '{"raw": "Palisades Park, NJ", "city": "Palisades Park", "state": "NJ"}'::jsonb,
 '{"min": 18.0, "max": 22.0, "unit": "hourly", "currency": "USD", "parsed": true, "raw": "$18-22/hr"}'::jsonb,
 'Bilingual retail manager. 주 5일 근무.', 22,
 '["retail"]'::jsonb,
 'bilingual', '2026-05-06', '2026-05-06 15:30:00+00',
 '{"record_id": "seed-010", "schema_version": "1.0"}'::jsonb),

-- 11. workingus / english / null salary / IL
('seed-011', 'workingus',
 'Chicago restaurant server — evening shift',
 'Kimchi Grill Chicago', false,
 '{"raw": "Chicago, IL", "city": "Chicago", "state": "IL"}'::jsonb,
 '{"min": null, "max": null, "unit": null, "currency": null, "parsed": false, "raw": "Negotiable"}'::jsonb,
 'Korean BBQ restaurant seeking evening servers.', 46,
 '["restaurant"]'::jsonb,
 'english', '2026-05-04', '2026-05-04 18:20:00+00',
 '{"record_id": "seed-011", "schema_version": "1.0"}'::jsonb),

-- 12. workingus / korean / yearly 50-60k / IL
('seed-012', 'workingus',
 '시카고 한인 회계법인 Staff Accountant 채용',
 'Lee & Kim CPAs', false,
 '{"raw": "Chicago, IL", "city": "Chicago", "state": "IL"}'::jsonb,
 '{"min": 50000.0, "max": 60000.0, "unit": "yearly", "currency": "USD", "parsed": true, "raw": "$50-60K"}'::jsonb,
 'CPA 준비생 및 경력자 모두 환영.', 20,
 '["office"]'::jsonb,
 'korean', '2026-05-03', '2026-05-03 09:50:00+00',
 '{"record_id": "seed-012", "schema_version": "1.0"}'::jsonb),

-- 13. workingus / english / hourly 15-18 / IL
('seed-013', 'workingus',
 'Hotel front desk associate — downtown Chicago',
 'Lakeview Hotel', true,
 '{"raw": "Chicago, IL", "city": "Chicago", "state": "IL"}'::jsonb,
 '{"min": 15.0, "max": 18.0, "unit": "hourly", "currency": "USD", "parsed": true, "raw": "$15-18/hr"}'::jsonb,
 'Greet guests, manage reservations. Nights/weekends.', 52,
 '["hotel"]'::jsonb,
 'english', '2026-05-02', '2026-05-02 17:10:00+00',
 '{"record_id": "seed-013", "schema_version": "1.0"}'::jsonb),

-- 14. wowseattle / korean / monthly 3500 / WA
('seed-014', 'wowseattle',
 '시애틀 뷰티살롱 매니저 구인',
 '뷰티플러스', false,
 '{"raw": "Bellevue, WA", "city": "Bellevue", "state": "WA"}'::jsonb,
 '{"min": 3500.0, "max": 4000.0, "unit": "monthly", "currency": "USD", "parsed": true, "raw": "월 $3500-4000"}'::jsonb,
 '뷰티살롱 총괄 매니저. 경력 3년 이상.', 23,
 '["beauty"]'::jsonb,
 'korean', '2026-05-01', '2026-05-01 12:30:00+00',
 '{"record_id": "seed-014", "schema_version": "1.0"}'::jsonb),

-- 15. wowseattle / english / yearly 40-50k / WA
('seed-015', 'wowseattle',
 'Seattle delivery driver (full-time)',
 'Pacific Logistics', false,
 '{"raw": "Seattle, WA", "city": "Seattle", "state": "WA"}'::jsonb,
 '{"min": 40000.0, "max": 50000.0, "unit": "yearly", "currency": "USD", "parsed": true, "raw": "$40-50K"}'::jsonb,
 'Daily routes across King County. CDL preferred.', 48,
 '["delivery"]'::jsonb,
 'english', '2026-04-30', '2026-04-30 14:05:00+00',
 '{"record_id": "seed-015", "schema_version": "1.0"}'::jsonb),

-- 16. radiokorea / korean / null salary / CA
('seed-016', 'radiokorea',
 'LA 한인 빌딩 청소 담당자 구인',
 '블루오션 클리닝', false,
 '{"raw": "Los Angeles, CA", "city": "Los Angeles", "state": "CA"}'::jsonb,
 '{"min": null, "max": null, "unit": null, "currency": null, "parsed": false, "raw": null}'::jsonb,
 '야간 청소 근무. 세부사항 면접 시 논의.', 24,
 '["cleaning"]'::jsonb,
 'korean', '2026-04-29', '2026-04-29 21:00:00+00',
 '{"record_id": "seed-016", "schema_version": "1.0"}'::jsonb),

-- 17. radiokorea / korean / yearly 55k / CA
('seed-017', 'radiokorea',
 'LA 자동차 부품 회사 사무직 채용',
 '현대모비스 아메리카', false,
 '{"raw": "Torrance, CA", "city": "Torrance", "state": "CA"}'::jsonb,
 '{"min": 55000.0, "max": 55000.0, "unit": "yearly", "currency": "USD", "parsed": true, "raw": "$55K/year"}'::jsonb,
 '자동차 부품 무역 담당. 한국어/영어 bilingual 우대.', 34,
 '["automotive", "office"]'::jsonb,
 'korean', '2026-04-28', '2026-04-28 10:00:00+00',
 '{"record_id": "seed-017", "schema_version": "1.0"}'::jsonb),

-- 18. koreadaily / korean / monthly 3000-5000 / CA
('seed-018', 'koreadaily',
 '배달 기사 구함 — LA 한인타운',
 null, false,
 '{"raw": "Los Angeles, CA", "city": "Los Angeles", "state": "CA"}'::jsonb,
 '{"min": 3000.0, "max": 5000.0, "unit": "monthly", "currency": "USD", "parsed": true, "raw": "$3-5k/mo"}'::jsonb,
 '본인 차량 소지자. 한인 식당 배달.', 22,
 '["delivery"]'::jsonb,
 'korean', '2026-05-09', '2026-05-09 15:00:00+00',
 '{"record_id": "seed-018", "schema_version": "1.0"}'::jsonb),

-- 19. koreadaily / korean / hourly 18-22 / CA
('seed-019', 'koreadaily',
 'LA 한인 미용실 스태프 구인',
 '뷰티로드 헤어살롱', false,
 '{"raw": "Los Angeles, CA", "city": "Los Angeles", "state": "CA"}'::jsonb,
 '{"min": 18.0, "max": 22.0, "unit": "hourly", "currency": "USD", "parsed": true, "raw": "$18-22/hr + tips"}'::jsonb,
 '미용 라이센스 소지자. 팁 포함.', 20,
 '["beauty"]'::jsonb,
 'korean', '2026-05-08', '2026-05-08 11:20:00+00',
 '{"record_id": "seed-019", "schema_version": "1.0"}'::jsonb),

-- 20. koreadaily / bilingual / null salary / CA
('seed-020', 'koreadaily',
 'LA 한인 교회 사무 (bilingual office assistant)',
 '은혜교회', false,
 '{"raw": "Fullerton, CA", "city": "Fullerton", "state": "CA"}'::jsonb,
 '{"min": null, "max": null, "unit": null, "currency": null, "parsed": false, "raw": "면접시"}'::jsonb,
 '교회 사무실 Office 관리. 한국어/영어 가능자.', 33,
 '["office"]'::jsonb,
 'bilingual', '2026-05-07', '2026-05-07 14:00:00+00',
 '{"record_id": "seed-020", "schema_version": "1.0"}'::jsonb),

-- 21. gtksa / bilingual / yearly 70k / GA
('seed-021', 'gtksa',
 '애틀랜타 건설 현장 Project Manager',
 'GA Construction Group', false,
 '{"raw": "Atlanta, GA", "city": "Atlanta", "state": "GA"}'::jsonb,
 '{"min": 70000.0, "max": 85000.0, "unit": "yearly", "currency": "USD", "parsed": true, "raw": "$70-85K"}'::jsonb,
 'Bilingual project manager for commercial construction.', 54,
 '["construction", "office"]'::jsonb,
 'bilingual', '2026-05-06', '2026-05-06 08:45:00+00',
 '{"record_id": "seed-021", "schema_version": "1.0"}'::jsonb),

-- 22. indeed / english / yearly 50-60k / FL
('seed-022', 'indeed',
 'Construction site supervisor',
 'Sunshine Builders', false,
 '{"raw": "Orlando, FL", "city": "Orlando", "state": "FL"}'::jsonb,
 '{"min": 50000.0, "max": 60000.0, "unit": "yearly", "currency": "USD", "parsed": true, "raw": "$50-60K"}'::jsonb,
 'Supervise residential site crews.', 33,
 '["construction", "general_labor"]'::jsonb,
 'english', '2026-05-05', '2026-05-05 09:15:00+00',
 '{"record_id": "seed-022", "schema_version": "1.0"}'::jsonb),

-- 23. linkedin / english / hourly 16-20 / TX
('seed-023', 'linkedin',
 'General laborer — Dallas distribution center',
 'TX DistroCo', true,
 '{"raw": "Dallas, TX", "city": "Dallas", "state": "TX"}'::jsonb,
 '{"min": 16.0, "max": 20.0, "unit": "hourly", "currency": "USD", "parsed": true, "raw": "$16-20/hr"}'::jsonb,
 'Load/unload trailers. Night shift differential.', 49,
 '["general_labor", "warehouse"]'::jsonb,
 'english', '2026-05-04', '2026-05-04 23:00:00+00',
 '{"record_id": "seed-023", "schema_version": "1.0"}'::jsonb),

-- 24. jobkoreausa / korean / yearly 40-50k / VA
('seed-024', 'jobkoreausa',
 '버지니아 한인 식당 주방 부주방장 모집',
 '한양관', false,
 '{"raw": "Annandale, VA", "city": "Annandale", "state": "VA"}'::jsonb,
 '{"min": 40000.0, "max": 50000.0, "unit": "yearly", "currency": "USD", "parsed": true, "raw": "$40-50K"}'::jsonb,
 '한식 주방 경력 3년 이상. 숙소 지원.', 25,
 '["restaurant"]'::jsonb,
 'korean', '2026-05-03', '2026-05-03 19:30:00+00',
 '{"record_id": "seed-024", "schema_version": "1.0"}'::jsonb),

-- 25. wowseattle / english / yearly 65-85k / WA
('seed-025', 'wowseattle',
 'Seattle caregiver coordinator (home health)',
 'PNW Home Care', false,
 '{"raw": "Seattle, WA", "city": "Seattle", "state": "WA"}'::jsonb,
 '{"min": 65000.0, "max": 85000.0, "unit": "yearly", "currency": "USD", "parsed": true, "raw": "$65-85k"}'::jsonb,
 'Coordinate in-home care across greater Seattle.', 49,
 '["caregiver", "healthcare"]'::jsonb,
 'english', '2026-05-02', '2026-05-02 12:00:00+00',
 '{"record_id": "seed-025", "schema_version": "1.0"}'::jsonb)

ON CONFLICT (record_id) DO NOTHING;

COMMIT;
