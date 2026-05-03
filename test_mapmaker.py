import asyncio
import sys
import os

# Add both researchOS and systemOS root
sys.path.append(os.path.abspath("."))

from systemOS.agents.mapmaker import build_map

async def main():
    topic = "recision Macro-Cycling for Body Composition: Researching the 2026 protocols for \"Carb Backloading\" vs. \"High-Protein Satiety\" models for fat loss without muscle wasting."
    res = await build_map(topic)
    print("=== RAW ===")
    print(repr(res.raw))
    print("=== CHAPTERS ===")
    print(res.total_chapters)

asyncio.run(main())
