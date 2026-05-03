-- ── Workout Week Planner ──────────────────────────────────────────────────────
-- Maps a day of week (0=Mon … 6=Sun) to a workout template for a given week
CREATE TABLE IF NOT EXISTS health.workout_plan_slots (
    id          SERIAL       PRIMARY KEY,
    user_id     INT          NOT NULL DEFAULT 1,
    week_start  DATE         NOT NULL,        -- Monday of the planned week
    day_of_week SMALLINT     NOT NULL,        -- 0=Mon, 1=Tue … 6=Sun
    template_id INT          REFERENCES health.workout_templates(id) ON DELETE SET NULL,
    label       VARCHAR(80),                 -- optional override name e.g. "Rest" or "Cardio"
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, week_start, day_of_week)
);

-- ── Meal Plan ─────────────────────────────────────────────────────────────────
-- One named meal plan covering a week
CREATE TABLE IF NOT EXISTS health.meal_plans (
    id          SERIAL       PRIMARY KEY,
    user_id     INT          NOT NULL DEFAULT 1,
    name        VARCHAR(120) NOT NULL DEFAULT 'My Meal Plan',
    week_start  DATE         NOT NULL,
    notes       TEXT,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, week_start)
);

-- Individual meal slots: day × meal_type → recipe or free-text meal
CREATE TABLE IF NOT EXISTS health.meal_plan_entries (
    id          SERIAL       PRIMARY KEY,
    plan_id     INT          NOT NULL REFERENCES health.meal_plans(id) ON DELETE CASCADE,
    day_of_week SMALLINT     NOT NULL,   -- 0=Mon … 6=Sun
    meal_type   VARCHAR(20)  NOT NULL,   -- breakfast/lunch/dinner/snack
    recipe_id   INT          REFERENCES health.recipes(id) ON DELETE SET NULL,
    custom_name VARCHAR(200),            -- free-text when no recipe
    servings    DECIMAL(5,2) NOT NULL DEFAULT 1,
    kcal        INT,
    protein_g   DECIMAL(7,2),
    carbs_g     DECIMAL(7,2),
    fat_g       DECIMAL(7,2),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
