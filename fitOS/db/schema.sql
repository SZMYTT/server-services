-- fitOS/db/schema.sql
-- Fitness and life coach tables.
-- Placeholder — expand when building fitOS.

-- Workouts
CREATE TABLE IF NOT EXISTS fit_workouts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date        DATE NOT NULL,
    type        TEXT NOT NULL,  -- run, cycle, lift, swim, yoga
    duration_mins INTEGER,
    distance_km NUMERIC(8,2),
    calories    INTEGER,
    heart_rate_avg INTEGER,
    notes       TEXT,
    source      TEXT,           -- garmin, strava, manual
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Daily check-ins
CREATE TABLE IF NOT EXISTS fit_checkins (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date        DATE UNIQUE NOT NULL,
    sleep_hrs   NUMERIC(4,1),
    sleep_score INTEGER,
    energy      INTEGER,        -- 1-10
    mood        INTEGER,        -- 1-10
    stress      INTEGER,        -- 1-10
    weight_kg   NUMERIC(5,2),
    notes       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Goals
CREATE TABLE IF NOT EXISTS fit_goals (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title       TEXT NOT NULL,
    category    TEXT,           -- weight, cardio, strength, habit
    target      TEXT,
    deadline    DATE,
    status      TEXT NOT NULL DEFAULT 'active',
    notes       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
