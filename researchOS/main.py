"""researchOS entry point — run from any directory."""
import os
import sys
from pathlib import Path

# researchOS project root (for local imports: web, agents, db, etc.)
sys.path.insert(0, str(Path(__file__).parent))
# server-services root (for shared systemOS imports)
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent / ".env")

import uvicorn
import logging

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "4001"))
    uvicorn.run("web.app:app", host="0.0.0.0", port=port, reload=False)
