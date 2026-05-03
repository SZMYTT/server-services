#!/bin/bash
# Pre-load the core stack into VRAM
ollama run gemma4:26b ""
ollama run gemma4:9b ""
ollama run llava:13b ""
echo "NNLOS Neural Stack is Warmed Up."