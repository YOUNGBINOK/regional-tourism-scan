CREATE EXTENSION IF NOT EXISTS postgis;
CREATE TABLE municipalities (
  code VARCHAR(10) PRIMARY KEY, name TEXT NOT NULL, province TEXT NOT NULL,
  tourism_type TEXT NOT NULL, geometry GEOMETRY(MultiPolygon, 4326), created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX municipalities_geometry_idx ON municipalities USING GIST (geometry);
CREATE TABLE tourism_monthly_metrics (
  municipality_code VARCHAR(10) REFERENCES municipalities(code), month DATE NOT NULL,
  external_visitors BIGINT, unique_visitors BIGINT, overnight_share NUMERIC(6,5), avg_stay_hours NUMERIC(8,3), avg_nights NUMERIC(8,3),
  tourism_card_spend BIGINT, demand_index NUMERIC(14,4), consumption_residual NUMERIC(14,5), attraction_concentration NUMERIC(6,5),
  night_tourism_index NUMERIC(12,5), seasonal_cv NUMERIC(12,5), source_version TEXT NOT NULL, PRIMARY KEY (municipality_code, month)
);
CREATE TABLE rgap_results (
  municipality_code VARCHAR(10) REFERENCES municipalities(code), month DATE NOT NULL,
  tcei NUMERIC(7,3) NOT NULL, frontier_tcei NUMERIC(7,3) NOT NULL, r_gap NUMERIC(7,3) NOT NULL,
  stay_gap NUMERIC(7,5), consumption_gap NUMERIC(7,5), dispersion_gap NUMERIC(7,5), stability_gap NUMERIC(7,5), model_version TEXT NOT NULL, PRIMARY KEY (municipality_code, month)
);
CREATE TABLE policy_experiments (
  id UUID PRIMARY KEY, municipality_code VARCHAR(10) REFERENCES municipalities(code), policy_name TEXT NOT NULL,
  treatment_start DATE NOT NULL, comparison_group JSONB NOT NULL, outcome_metric TEXT NOT NULL, did_estimate NUMERIC(14,5), created_at TIMESTAMPTZ DEFAULT now()
);
COMMENT ON COLUMN tourism_monthly_metrics.consumption_residual IS 'Demand-adjusted regression residual; do not replace with raw spend/visitor ratio.';
