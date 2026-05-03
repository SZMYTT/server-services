import json
from datetime import datetime, timedelta
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from db import get_conn

def generate_weekly_shopping_list(days: int = 7):
    """
    Parses the weekly meal plan (meal_logs for the next `days` or past `days` if planning ahead)
    and generates a consolidated grocery list grouped by aisle.
    For simplicity in this phase, we look at the recipes logged in the past week as the 'plan'.
    """
    # Using past 7 days of meal logs to generate a recurring list or future plan
    days_ago = datetime.now() - timedelta(days=days)
    
    shopping_list = {}
    
    # Aisle categorization based on ingredient name keywords (simple heuristic)
    aisles = {
        "Produce": ["apple", "banana", "onion", "garlic", "carrot", "tomato", "potato", "spinach", "lettuce", "pepper"],
        "Meat & Seafood": ["chicken", "beef", "pork", "fish", "salmon", "turkey", "steak"],
        "Dairy & Eggs": ["milk", "cheese", "egg", "butter", "yogurt", "cream"],
        "Pantry": ["rice", "pasta", "bean", "oil", "sauce", "sugar", "salt", "spice", "flour", "bread", "oat"]
    }
    
    def get_aisle(name: str) -> str:
        name_lower = name.lower()
        for aisle, keywords in aisles.items():
            for kw in keywords:
                if kw in name_lower:
                    return aisle
        return "Other"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT i.name, i.brand, ri.quantity_g
                FROM health.meal_logs ml
                JOIN health.recipes r ON ml.recipe_id = r.id
                JOIN health.recipe_ingredients ri ON r.id = ri.recipe_id
                JOIN health.ingredients i ON i.id = ri.ingredient_id
                WHERE ml.consumed_at >= %s
            """, (days_ago,))
            
            for row in cur.fetchall():
                name = row[0]
                brand = row[1]
                qty = float(row[2])
                
                aisle = get_aisle(name)
                
                item_key = f"{name}" + (f" ({brand})" if brand else "")
                
                if aisle not in shopping_list:
                    shopping_list[aisle] = {}
                
                if item_key not in shopping_list[aisle]:
                    shopping_list[aisle][item_key] = 0.0
                
                shopping_list[aisle][item_key] += qty
                
    return shopping_list

if __name__ == "__main__":
    s_list = generate_weekly_shopping_list()
    print(json.dumps(s_list, indent=2))
