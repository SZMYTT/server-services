import sys
import os
import asyncio
from pathlib import Path
from dotenv import load_dotenv

# Set up paths to allow importing PrismaOS modules
BASE_DIR = Path("/home/szmyt/server-services/prismaOS")
sys.path.append(str(BASE_DIR))
sys.path.append(str(BASE_DIR.parent))

# Terminal colors for easy reading
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
RESET = '\033[0m'

# Load environment variables
load_dotenv(BASE_DIR / ".env")

def report(name, status, message=""):
    """Prints formatted diagnostic output."""
    if status == "LIVE":
        print(f"[{GREEN}LIVE{RESET}] {name}: {message}")
    elif status == "PENDING":
        print(f"[{YELLOW}PENDING{RESET}] {name}: {message}")
    else:
        print(f"[{RED}BROKEN{RESET}] {name}: {message}")

async def run_diagnostics():
    print("\n🧪 NNLOS / PrismaOS Architecture Diagnostics\n" + "="*55)

    # 1. SOP Assembly Check
    try:
        from systemOS.services.sop_assembler import assemble_sop
        sop = assemble_sop("research", "research", "operator", persona="architect")
        if sop and len(sop) > 100:
            report("SOP Assembly Check", "LIVE", f"Successfully generated {len(sop)} character prompt.")
        else:
            report("SOP Assembly Check", "BROKEN", "Generated SOP is empty or unusually short.")
    except Exception as e:
        report("SOP Assembly Check", "BROKEN", str(e))

    # 2 & 3. Auditor Failure & Mapmaker Schema
    expert_panel_path = BASE_DIR.parent / "systemOS" / "services" / "expert_panel.py"
    mapmaker_path = BASE_DIR.parent / "systemOS" / "agents" / "mapmaker.py"
    
    if expert_panel_path.exists():
        report("Auditor Failure Trigger", "LIVE", "expert_panel.py agent found.")
    else:
        report("Auditor Failure Trigger", "PENDING", "expert_panel.py not implemented yet.")
        
    if mapmaker_path.exists():
        report("Mapmaker Schema Validation", "LIVE", "mapmaker.py agent found.")
    else:
        report("Mapmaker Schema Validation", "PENDING", "mapmaker.py not implemented yet.")

    # 4. Thorough Interception
    try:
        orch_path = BASE_DIR / "services" / "orchestrator.py"
        content = orch_path.read_text()
        if "Implementation for Mapmaker and parallel Expert Panel tasks goes here" in content:
            report("Thorough Interception", "PENDING", "run_thorough_research is routed but currently a stub.")
        elif "def run_thorough_research" in content:
            report("Thorough Interception", "LIVE", "Parallel execution logic found in orchestrator.py.")
        else:
            report("Thorough Interception", "BROKEN", "Missing run_thorough_research router logic.")
    except Exception as e:
        report("Thorough Interception", "BROKEN", str(e))

    # 5. Vision-Action Loop
    try:
        from systemOS.mcp.browser import InteractiveBrowser
        if hasattr(InteractiveBrowser, "capture_screenshot"):
            report("Vision-Action Loop", "LIVE", "InteractiveBrowser supports Playwright screenshots.")
        else:
            report("Vision-Action Loop", "BROKEN", "Missing capture_screenshot method.")
    except Exception as e:
        report("Vision-Action Loop", "BROKEN", str(e))

    # 6. Human-in-the-Loop (HITL) Gate
    try:
        from agents.web_operator import _VISION_PROMPT
        if "hitl_required" in _VISION_PROMPT:
            report("Human-in-the-Loop Gate", "LIVE", "'hitl_required' logic is active in the vision prompt.")
        else:
            report("Human-in-the-Loop Gate", "BROKEN", "HITL instructions missing from prompt.")
    except Exception as e:
        report("Human-in-the-Loop Gate", "BROKEN", str(e))

    # 7. VRAM Semaphore
    try:
        router_path = BASE_DIR / "services" / "router.py"
        if router_path.exists() and "Semaphore" in router_path.read_text():
            report("VRAM Semaphore Test", "LIVE", "asyncio.Semaphore detected in routing logic.")
        else:
            report("VRAM Semaphore Test", "PENDING", "Global VRAM_SEMAPHORE not yet implemented.")
    except Exception as e:
        report("VRAM Semaphore Test", "BROKEN", str(e))

    # 8. Graph Extraction
    try:
        from mcp.graph import graph_db
        await graph_db.connect()
        if graph_db.driver:
            report("Graph Extraction Memory", "LIVE", "Neo4j database connection successful.")
            await graph_db.close()
        else:
            report("Graph Extraction Memory", "BROKEN", "Could not connect to Neo4j. Check Docker container.")
    except Exception as e:
        report("Graph Extraction Memory", "BROKEN", str(e))

    print("\n" + "="*55 + "\nDiagnostics Complete. Review PENDING items to continue implementation.\n")

if __name__ == "__main__":
    asyncio.run(run_diagnostics())