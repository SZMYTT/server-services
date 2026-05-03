import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from db import get_conn

logger = logging.getLogger(__name__)

def generate_context_last_7_days(format="markdown") -> str:
    """Exports the last 7 days of health data into Markdown or JSON."""
    days_ago = datetime.now() - timedelta(days=7)
    
    data = {
        "metrics": [],
        "workouts": [],
        "meals": []
    }
    
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 1. Metrics
            cur.execute("""
                SELECT metric_type, value, unit, created_at, note 
                FROM health.metrics 
                WHERE created_at >= %s
                ORDER BY created_at ASC
            """, (days_ago,))
            for row in cur.fetchall():
                data["metrics"].append({
                    "type": row[0],
                    "value": float(row[1]),
                    "unit": row[2],
                    "date": row[3].isoformat(),
                    "note": row[4]
                })
                
            # 2. Workouts
            cur.execute("""
                SELECT id, name, started_at, finished_at, notes
                FROM health.workout_logs
                WHERE started_at >= %s
                ORDER BY started_at ASC
            """, (days_ago,))
            for row in cur.fetchall():
                log_id = row[0]
                workout = {
                    "id": log_id,
                    "name": row[1],
                    "started_at": row[2].isoformat(),
                    "finished_at": row[3].isoformat() if row[3] else None,
                    "notes": row[4],
                    "sets": []
                }
                # Fetch sets
                cur.execute("""
                    SELECT e.name, ws.set_number, ws.weight, ws.reps, ws.rir
                    FROM health.workout_sets ws
                    JOIN health.exercises e ON e.id = ws.exercise_id
                    WHERE ws.log_id = %s
                    ORDER BY ws.logged_at ASC
                """, (log_id,))
                for s_row in cur.fetchall():
                    workout["sets"].append({
                        "exercise": s_row[0],
                        "set": s_row[1],
                        "weight": float(s_row[2]) if s_row[2] else None,
                        "reps": s_row[3],
                        "rir": s_row[4]
                    })
                data["workouts"].append(workout)
                
            # 3. Meals
            cur.execute("""
                SELECT ml.meal_name, ml.meal_type, ml.consumed_at, r.name as recipe_name
                FROM health.meal_logs ml
                LEFT JOIN health.recipes r ON r.id = ml.recipe_id
                WHERE ml.consumed_at >= %s
                ORDER BY ml.consumed_at ASC
            """, (days_ago,))
            for row in cur.fetchall():
                data["meals"].append({
                    "name": row[3] if row[3] else row[0],
                    "type": row[1],
                    "date": row[2].isoformat()
                })
                
    if format == "json":
        return json.dumps(data, indent=2)
        
    # Markdown format
    md = "# HealthOS Context (Last 7 Days)\n\n"
    
    md += "## Metrics\n"
    for m in data["metrics"]:
        md += f"- **{m['date'][:10]}**: {m['type']} = {m['value']} {m['unit']}"
        if m['note']:
            md += f" ({m['note']})"
        md += "\n"
        
    md += "\n## Workouts\n"
    for w in data["workouts"]:
        md += f"### {w['name']} ({w['started_at'][:10]})\n"
        for s in w['sets']:
            weight = f"{s['weight']}kg" if s['weight'] else "Bodyweight"
            md += f"- {s['exercise']} Set {s['set']}: {s['reps']} reps @ {weight} (RIR: {s['rir']})\n"
            
    md += "\n## Meals\n"
    for m in data["meals"]:
        md += f"- **{m['date'][:10]}** [{m['type']}]: {m['name']}\n"
        
    return md

if __name__ == "__main__":
    print(generate_context_last_7_days(format="markdown"))
