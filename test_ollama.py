import requests

system = """
Return ONLY a valid JSON object. No explanation, no markdown, no preamble. Do NOT wrap the JSON in ```json blocks. The first character of your response MUST be { and the last character MUST be }.
"""

payload = {
    "model": "nnl-planner:latest",
    "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": "Decompose this topic: fitness into 2 volumes and 2 chapters each"}
    ],
    "stream": False,
    "max_tokens": 1000
}

r = requests.post("http://100.76.139.41:11434/v1/chat/completions", json=payload)
print(r.status_code)
data = r.json()
print("CONTENT:", data['choices'][0]['message']['content'])
