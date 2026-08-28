-- National benchmark, structural peer group, and explainable diagnosis storage.
-- Run after 001_schema.sql. These tables intentionally separate observed data
-- from derived peer-model output.

CREATE TABLE IF NOT EXISTS municipality_structural_profiles (
  municipality_code VARCHAR(10) REFERENCES municipalities(code),
  as_of_year SMALLINT NOT NULL,
  population BIGINT,
  area_sq_km NUMERIC(12,3),
  population_density NUMERIC(14,3),
  urban_rural_class TEXT,
  coastal_or_island BOOLEAN,
  accessibility_score NUMERIC(8,4),
  lodging_business_count INTEGER,
  verified_room_count INTEGER,
  tourism_resource_count INTEGER,
  source_versions JSONB NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (municipality_code, as_of_year)
);

CREATE TABLE IF NOT EXISTS national_metric_benchmarks (
  month DATE NOT NULL,
  metric_code TEXT NOT NULL,
  municipality_code VARCHAR(10) REFERENCES municipalities(code),
  observed_value NUMERIC(18,5),
  national_percentile NUMERIC(6,3),
  source_version TEXT NOT NULL,
  PRIMARY KEY (month, metric_code, municipality_code)
);

CREATE TABLE IF NOT EXISTS peer_group_memberships (
  month DATE NOT NULL,
  model_version TEXT NOT NULL,
  target_municipality_code VARCHAR(10) REFERENCES municipalities(code),
  peer_municipality_code VARCHAR(10) REFERENCES municipalities(code),
  structural_distance NUMERIC(16,8) NOT NULL,
  rank SMALLINT NOT NULL,
  feature_snapshot JSONB NOT NULL,
  PRIMARY KEY (month, model_version, target_municipality_code, peer_municipality_code),
  CHECK (target_municipality_code <> peer_municipality_code)
);

CREATE TABLE IF NOT EXISTS municipality_policy_diagnoses (
  municipality_code VARCHAR(10) REFERENCES municipalities(code),
  month DATE NOT NULL,
  model_version TEXT NOT NULL,
  demand_type TEXT NOT NULL,
  diagnosis_type TEXT NOT NULL,
  diagnosis_summary TEXT NOT NULL,
  root_causes JSONB NOT NULL DEFAULT '[]'::jsonb,
  policy_priorities JSONB NOT NULL DEFAULT '[]'::jsonb,
  peer_summary JSONB NOT NULL,
  data_completeness JSONB NOT NULL,
  PRIMARY KEY (municipality_code, month, model_version)
);

CREATE INDEX IF NOT EXISTS national_metric_benchmarks_lookup_idx
  ON national_metric_benchmarks (month, metric_code, national_percentile);
CREATE INDEX IF NOT EXISTS peer_group_memberships_target_idx
  ON peer_group_memberships (month, model_version, target_municipality_code, rank);
