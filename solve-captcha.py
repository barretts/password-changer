#!/usr/bin/env python3
"""Send a CAPTCHA screenshot to a local vision LLM and return the solution as JSON."""

import base64
import json
import sys
from pathlib import Path

import urllib.request

VISION_LLM_URL = "http://192.168.1.129:1234/v1/chat/completions"
VISION_LLM_MODEL = "qwen/qwen3-vl-8b"


def encode_image(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def solve(image_path: str, mode: str = "auto", instruction: str = "") -> dict:
    b64 = encode_image(image_path)
    ext = Path(image_path).suffix.lstrip(".").lower()
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext, "image/png")

    if mode == "grid" and instruction:
        user_text = (
            f"This is a CAPTCHA image. The instruction is: \"{instruction}\"\n"
            "Return a JSON object with: {\"type\": \"grid\", \"cells\": [list of 1-indexed cell numbers to click], \"grid_size\": \"3x3\" or \"4x4\"}\n"
            "Return ONLY the JSON, nothing else."
        )
    else:
        user_text = (
            "This is a screenshot of a web page that may contain a CAPTCHA challenge.\n"
            "Analyze the image and return a JSON object describing what you see:\n"
            "- If it's a grid/image CAPTCHA: {\"type\": \"grid\", \"cells\": [1-indexed cell numbers], \"grid_size\": \"3x3\" or \"4x4\", \"instruction\": \"what it asks\"}\n"
            "- If it's a text CAPTCHA: {\"type\": \"text\", \"solution\": \"the text to type\"}\n"
            "- If it's a checkbox (I'm not a robot): {\"type\": \"checkbox\"}\n"
            "- If it's a Cloudflare Turnstile or behavioral challenge: {\"type\": \"behavioral\", \"description\": \"what you see\"}\n"
            "- If no CAPTCHA is visible: {\"type\": \"none\", \"description\": \"what the page shows\"}\n"
            "- If you can't determine: {\"type\": \"unknown\"}\n"
            "Return ONLY the JSON, nothing else."
        )

    payload = {
        "model": VISION_LLM_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 4096,
        "temperature": 0.1,
    }

    req = urllib.request.Request(
        VISION_LLM_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    choice = data["choices"][0]["message"]
    content = choice.get("content", "")
    if not content:
        content = choice.get("reasoning_content", "")

    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        content = "\n".join(lines).strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {"type": "unknown", "raw": content[:500]}


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <image_path> [mode] [instruction]", file=sys.stderr)
        sys.exit(1)

    image_path = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "auto"
    instruction = sys.argv[3] if len(sys.argv) > 3 else ""

    result = solve(image_path, mode, instruction)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
