text = ""
for block in data.get("content", []):
    if block.get("type") == "text":
        text += block.get("text", "")

if not text.strip():
    raise ValueError(f"Empty response from Claude. Full response: {json.dumps(data)}")

text = text.strip()
start = text.find('{')
end = text.rfind('}') + 1
if start >= 0 and end > start:
    text = text[start:end]

return json.loads(text)
