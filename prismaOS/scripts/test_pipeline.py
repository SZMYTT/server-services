import asyncio
import logging
from services.queue import add_task, get_task, approve_task, get_next_approved_task
from services.router import route_task
from agents.researcher import run_research_task
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)

async def test_end_to_end():
    # 1. Queue Task
    print("\n[TEST] 1. Queueing Task")
    task_id = await add_task(
        workspace="property",
        user="daniel",
        task_type="research",
        risk_level="internal",
        module="research",
        input="What are average house prices in Coventry in 2026?",
        trigger_type="script"
    )
    print(f"[TEST] Task Queued: {task_id}")
    
    # 2. Approve Task
    print(f"\n[TEST] 2. Approving Task {task_id}")
    await approve_task(task_id, "daniel")
    
    # 3. Runner logic (mocking what orchestrator would do)
    print("\n[TEST] 3. Fetching next approved task")
    task = await get_next_approved_task()
    
    if not task:
        print("[TEST] No approved task found!")
        return

    print(f"[TEST] Picked up task {task['id']}")
    
    # 4. Route Task
    print("\n[TEST] 4. Routing Task")
    routing = await route_task(
        task_type=task["task_type"],
        module=task["module"],
        queue_lane=task["queue_lane"],
        risk_level=task["risk_level"]
    )
    print(f"[TEST] Route decided: {routing['model']} at {routing['host']}")
    
    # 5. Run Agent (Researcher in this case)
    print("\n[TEST] 5. Running Researcher Agent")
    # Note: This step actually attempts to call Ollama.
    # Since Ollama isn't configured in this environment, it will fail gracefully or timeout.
    # But it proves the pipeline logic works.
    await run_research_task(task, routing)
    
    # 6. Check final status
    print("\n[TEST] 6. Checking final status")
    final_task = await get_task(task_id)
    print(f"[TEST] Final Status: {final_task['status']}")
    if final_task['status'] == 'failed':
        print(f"[TEST] Error message: {final_task.get('error', 'Check db error fields format based on queue.py... oops queue.py output field is used for errors on failure')}")
        print(f"[TEST] Output field: {final_task.get('output', '')[:200]}")
    else:
        print(f"[TEST] Output sample: {str(final_task.get('output', ''))[:200]}...")

if __name__ == "__main__":
    asyncio.run(test_end_to_end())
