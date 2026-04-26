# fitOS

**Personal fitness and life coach — Android / web.**

fitOS is a personal AI coach built on SystemOS. It connects to wearable data (Garmin, Strava), tracks workouts, nutrition, and habits, and gives Daniel proactive daily coaching via mobile and web.

## Status

`planned` — scaffold only. Wire in when ready.

## Planned features

- **Workout tracking** — log sessions, sets, reps, cardio; analyse trends
- **Nutrition logging** — meal tracking, macros, calorie targets
- **Garmin/Strava sync** — pull activity data automatically
- **Daily check-in** — AI morning brief: sleep score, readiness, plan for the day
- **Goal tracking** — set targets (weight, VO2max, race time), track progress
- **Life coaching** — habits, mood, sleep, stress — weekly review
- **Android app** — native mobile UI (planned: React Native or Flutter)
- **Web dashboard** — progress charts, coach chat interface

## Planned integrations (MCP)

| MCP | Source | Notes |
|-----|--------|-------|
| `mcp/garmin.py` | Garmin Connect API | Heart rate, sleep, steps, workouts |
| `mcp/strava.py` | Strava API | Runs, rides, swimming |
| `mcp/nutrition.py` | OpenFoodFacts / Cronometer | Food lookup |

## SystemOS integration

fitOS will use `add_task()` from SystemOS to queue AI coaching tasks:

```python
import sys
sys.path.insert(0, '/home/szmyt/server-services')
from systemOS.services.queue import add_task
```

## Structure

```
fitOS/
├── services/          # fitOS-specific services
├── agents/            # Coaching agent implementations
├── mcp/               # Garmin, Strava, nutrition MCPs
├── web/               # Web dashboard (FastAPI + Jinja2)
├── android/           # Android app (React Native / Flutter)
├── db/
│   └── schema.sql     # Fitness tables
├── config/
├── environment.yaml
└── .env.example
```
