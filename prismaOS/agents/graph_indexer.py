import logging
import json
import re
from mcp.graph import graph_db
from services.queue import set_task_status

from systemOS.llm import complete_ex 

logger = logging.getLogger("prisma.agents.graph_indexer")

_SYSTEM_PROMPT = """You are a background Graph Data Extractor for PrismaOS.
Your job is to read business text and extract entity-relationship triples to build "Institutional Wisdom".

Output ONLY a valid JSON list of triples. No markdown, no explanations, no code block backticks.

Valid Node Labels: Workspace, Vendor, Product, Person, Project, Region, PricePoint
Valid Relations: SUPPLIES, OWNS, HAS_PRICE_TIER, LOCATED_IN, COMPETES_WITH, RELATED_TO

Format exactly like this example:
[
  {
    "source_label": "Vendor",
    "source_id": "Cargill",
    "source_props": {"name": "Cargill"},
    "relation_type": "SUPPLIES",
    "target_label": "Product",
    "target_id": "C3 Soy Wax",
    "target_props": {"name": "C3 Soy Wax", "sku": "C3"}
  }
]
"""

async def run_graph_indexer_task(task: dict, routing: dict):
    """Extracts relational memory from an agent's text output and upserts to Neo4j."""
    task_id = task["id"]
    input_text = task.get("input", "")
    
    if not input_text:
        logger.warning(f"[GRAPH_INDEXER] Task {task_id} has no input text. Skipping.")
        await set_task_status(task_id, "done", output="No input provided.")
        return

    try:
        logger.info(f"[GRAPH_INDEXER] Extracting graph entities for task {task_id}")
        
        # Force the fast router model (runs on MacBook Pro per AGENTS.md rules)
        model = routing.get("model", "llama3.2:3b")
        
        llm_result = await complete_ex(
            messages=[{"role": "user", "content": f"Extract relationships from the following text:\n\n{input_text}"}],
            system=_SYSTEM_PROMPT,
            model=model,
            max_tokens=2000
        )
        
        output_text = llm_result.get("text", "[]")
        output_text = re.sub(r"```json\s*", "", output_text)
        output_text = re.sub(r"```", "", output_text).strip()
        
        triples = json.loads(output_text)
        
        success_count = 0
        for t in triples:
            ok = await graph_db.add_entity_relation(
                source_label=t.get("source_label", "Entity"),
                source_id=t.get("source_id", "Unknown"),
                source_props=t.get("source_props", {}),
                relation_type=t.get("relation_type", "RELATED_TO"),
                target_label=t.get("target_label", "Entity"),
                target_id=t.get("target_id", "Unknown"),
                target_props=t.get("target_props", {})
            )
            if ok: success_count += 1
        
        await set_task_status(task_id, "done", output=f"Extracted and merged {success_count}/{len(triples)} relations into Neo4j.")
    except Exception as e:
        logger.error(f"[GRAPH_INDEXER] Error in task {task_id}: {e}")
        await set_task_status(task_id, "failed", output=str(e))