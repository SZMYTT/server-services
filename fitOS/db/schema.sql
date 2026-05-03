-- fitOS/db/schema.sql
-- All fitOS tables live in the health.* schema inside the shared systemos database.

CREATE SCHEMA IF NOT EXISTS health;

-- ── Phase 1: generic metric log ───────────────────────────────────────────────
-- One row per logged data point. metric_type drives which module owns the row.
-- Phase 1: 'weight'
-- Future:  'sleep_hrs', 'sleep_score', 'hrv', 'resting_hr', 'steps',
--          'calories', 'water_ml', 'energy', 'mood', 'stress'

CREATE TABLE IF NOT EXISTS health.metrics (
    id          SERIAL       PRIMARY KEY,
    user_id     INT          NOT NULL DEFAULT 1,
    metric_type VARCHAR(50)  NOT NULL,
    value       DECIMAL(8,2) NOT NULL,
    unit        VARCHAR(20)  NOT NULL,
    note        TEXT,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_health_metrics_type_time
    ON health.metrics (metric_type, created_at DESC);

-- ── Workouts (Phase 2) ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS health.workouts (
    id             SERIAL       PRIMARY KEY,
    user_id        INT          NOT NULL DEFAULT 1,
    date           DATE         NOT NULL,
    workout_type   TEXT         NOT NULL,  -- run, lift, cycle, swim, yoga
    duration_mins  INT,
    distance_km    DECIMAL(8,2),
    calories       INT,
    heart_rate_avg INT,
    notes          TEXT,
    source         TEXT,                   -- garmin, strava, manual
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── Daily check-ins (Phase 2) ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS health.checkins (
    id         SERIAL      PRIMARY KEY,
    user_id    INT         NOT NULL DEFAULT 1,
    date       DATE        NOT NULL,
    notes      TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, date)
);

-- ── Goals (Phase 3) ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS health.goals (
    id         SERIAL      PRIMARY KEY,
    user_id    INT         NOT NULL DEFAULT 1,
    title      TEXT        NOT NULL,
    category   TEXT,                      -- weight, cardio, strength, habit
    target     TEXT,
    deadline   DATE,
    status     TEXT        NOT NULL DEFAULT 'active',
    notes      TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Phase 2: Workout module ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS health.exercises (
    id           SERIAL       PRIMARY KEY,
    name         VARCHAR(200) NOT NULL,
    muscle_group VARCHAR(100),
    equipment    VARCHAR(100),
    notes        TEXT,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS health.workout_templates (
    id         SERIAL       PRIMARY KEY,
    user_id    INT          NOT NULL DEFAULT 1,
    name       VARCHAR(200) NOT NULL,
    notes      TEXT,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS health.template_exercises (
    id            SERIAL       PRIMARY KEY,
    template_id   INT          NOT NULL REFERENCES health.workout_templates(id) ON DELETE CASCADE,
    exercise_id   INT          NOT NULL REFERENCES health.exercises(id),
    order_index   INT          NOT NULL DEFAULT 0,
    target_sets   INT,
    target_reps   VARCHAR(20),            -- e.g. "8-12"
    target_weight DECIMAL(6,2)
);

CREATE TABLE IF NOT EXISTS health.workout_logs (
    id          SERIAL       PRIMARY KEY,
    user_id     INT          NOT NULL DEFAULT 1,
    template_id INT          REFERENCES health.workout_templates(id),
    name        VARCHAR(200),
    started_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS health.workout_sets (
    id          SERIAL       PRIMARY KEY,
    log_id      INT          NOT NULL REFERENCES health.workout_logs(id) ON DELETE CASCADE,
    exercise_id INT          NOT NULL REFERENCES health.exercises(id),
    set_number  INT          NOT NULL,
    weight      DECIMAL(6,2),
    reps        INT,
    rir         SMALLINT,                 -- Reps in Reserve (0 = failure)
    volume      DECIMAL(8,2) GENERATED ALWAYS AS (weight * reps) STORED,
    logged_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_workout_sets_exercise
    ON health.workout_sets (exercise_id, logged_at DESC);

-- ── Phase 2: Nutrition module ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS health.ingredients (
    id              SERIAL       PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,
    barcode         VARCHAR(50),
    brand           VARCHAR(200),
    serving_size_g  DECIMAL(6,2) NOT NULL DEFAULT 100,
    kcal            DECIMAL(7,2),
    protein_g       DECIMAL(6,2),
    carbs_g         DECIMAL(6,2),
    fat_g           DECIMAL(6,2),
    fibre_g         DECIMAL(6,2),
    source          VARCHAR(50)  NOT NULL DEFAULT 'manual', -- 'manual', 'openfoodfacts'
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ingredients_barcode
    ON health.ingredients (barcode) WHERE barcode IS NOT NULL;

CREATE TABLE IF NOT EXISTS health.recipes (
    id         SERIAL       PRIMARY KEY,
    user_id    INT          NOT NULL DEFAULT 1,
    name       VARCHAR(200) NOT NULL,
    notes      TEXT,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS health.recipe_ingredients (
    id            SERIAL       PRIMARY KEY,
    recipe_id     INT          NOT NULL REFERENCES health.recipes(id) ON DELETE CASCADE,
    ingredient_id INT          NOT NULL REFERENCES health.ingredients(id),
    quantity_g    DECIMAL(7,2) NOT NULL
);

-- ── Phase 3: OAuth token vault ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS health.oauth_tokens (
    id            SERIAL       PRIMARY KEY,
    user_id       INT          NOT NULL DEFAULT 1,
    provider      VARCHAR(50)  NOT NULL,          -- 'fitbit'
    access_token  TEXT         NOT NULL,
    refresh_token TEXT         NOT NULL,
    expires_at    TIMESTAMPTZ  NOT NULL,
    scope         TEXT,
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, provider)
);

-- ── Phase 3: Meal logs ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS health.meal_logs (
    id           SERIAL       PRIMARY KEY,
    user_id      INT          NOT NULL DEFAULT 1,
    recipe_id    INT          REFERENCES health.recipes(id) ON DELETE SET NULL,
    meal_name    VARCHAR(200),                    -- free-text if no recipe
    meal_type    VARCHAR(20)  NOT NULL DEFAULT 'meal', -- breakfast/lunch/dinner/snack/meal
    consumed_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_meal_logs_user_day
    ON health.meal_logs (user_id, consumed_at DESC);

-- ── Phase 4: Intelligence & Planning ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS health.mesocycles (
    id          SERIAL       PRIMARY KEY,
    user_id     INT          NOT NULL DEFAULT 1,
    name        VARCHAR(200) NOT NULL,
    start_date  DATE         NOT NULL,
    end_date    DATE         NOT NULL,
    objective   TEXT,
    notes       TEXT,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── Phase 5: User daily targets ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS health.user_targets (
    id             SERIAL       PRIMARY KEY,
    user_id        INT          NOT NULL DEFAULT 1,
    kcal           INT,
    protein_g      DECIMAL(6,2),
    carbs_g        DECIMAL(6,2),
    fat_g          DECIMAL(6,2),
    water_ml       INT          NOT NULL DEFAULT 2500,
    effective_from DATE         NOT NULL DEFAULT CURRENT_DATE,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── Phase 5: Progress photos ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS health.progress_photos (
    id          SERIAL       PRIMARY KEY,
    user_id     INT          NOT NULL DEFAULT 1,
    date        DATE         NOT NULL DEFAULT CURRENT_DATE,
    filepath    TEXT         NOT NULL,
    weight_kg   DECIMAL(5,2),
    notes       TEXT,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── Feature 2: Biomarker & Blood Test Vault ───────────────────────────────────

-- Dictionary of trackable biomarkers with optimal reference ranges
CREATE TABLE IF NOT EXISTS health.biomarker_dictionary (
    id          SERIAL       PRIMARY KEY,
    name        VARCHAR(120) NOT NULL UNIQUE,
    unit        VARCHAR(30)  NOT NULL,
    range_min   DECIMAL(10,4),
    range_max   DECIMAL(10,4),
    category    VARCHAR(60),          -- e.g. 'Hormones', 'Vitamins', 'Lipids'
    notes       TEXT,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Individual blood test events (date + lab)
CREATE TABLE IF NOT EXISTS health.blood_test_events (
    id          SERIAL       PRIMARY KEY,
    user_id     INT          NOT NULL DEFAULT 1,
    test_date   DATE         NOT NULL,
    lab_name    VARCHAR(120),
    notes       TEXT,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Results: link each event to multiple biomarker values
CREATE TABLE IF NOT EXISTS health.biomarker_results (
    id            SERIAL       PRIMARY KEY,
    event_id      INT          NOT NULL REFERENCES health.blood_test_events(id) ON DELETE CASCADE,
    biomarker_id  INT          NOT NULL REFERENCES health.biomarker_dictionary(id),
    value         DECIMAL(12,4) NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Seed common biomarkers if none exist
INSERT INTO health.biomarker_dictionary (name, unit, range_min, range_max, category) VALUES
  ('Vitamin D (25-OH)',      'nmol/L', 75,   200,  'Vitamins'),
  ('Free Testosterone',      'pmol/L', 174,  729,  'Hormones'),
  ('Total Testosterone',     'nmol/L', 8.64, 29,   'Hormones'),
  ('SHBG',                   'nmol/L', 18.3, 54.1, 'Hormones'),
  ('Ferritin',               'µg/L',   30,   400,  'Iron'),
  ('Serum Iron',             'µmol/L', 11,   30,   'Iron'),
  ('TSH',                    'mIU/L',  0.27, 4.2,  'Thyroid'),
  ('Free T4',                'pmol/L', 12,   22,   'Thyroid'),
  ('HbA1c',                  '%',      4,    5.6,  'Glucose'),
  ('Fasting Glucose',        'mmol/L', 3.9,  5.6,  'Glucose'),
  ('Total Cholesterol',      'mmol/L', 0,    5.2,  'Lipids'),
  ('LDL Cholesterol',        'mmol/L', 0,    3.0,  'Lipids'),
  ('HDL Cholesterol',        'mmol/L', 1.0,  99,   'Lipids'),
  ('Triglycerides',          'mmol/L', 0,    1.7,  'Lipids'),
  ('CRP (hsCRP)',             'mg/L',   0,    1.0,  'Inflammation'),
  ('Creatinine',             'µmol/L', 62,   115,  'Kidney'),
  ('eGFR',                   'mL/min', 90,   999,  'Kidney'),
  ('ALT',                    'U/L',    7,    56,   'Liver'),
  ('AST',                    'U/L',    10,   40,   'Liver'),
  ('Haemoglobin',            'g/dL',   13.5, 17.5, 'Haematology'),
  ('Cortisol (AM)',          'nmol/L', 140,  700,  'Hormones'),
  ('Insulin-like Growth Factor 1 (IGF-1)', 'nmol/L', 11, 36, 'Hormones')
ON CONFLICT (name) DO NOTHING;

