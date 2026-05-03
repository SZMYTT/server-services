import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

# Both fitOS/ and server-services/ on path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import uvicorn
from db import init_schema

if __name__ == "__main__":
    init_schema()
    uvicorn.run(
        "web.app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 4002)),
        reload=False,
    )
