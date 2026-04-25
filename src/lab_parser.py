"""
lab_parser.py — Lab PDF/image extraction via Claude API.

Sends the dropped file to Claude as a document or image and asks it to
extract the four cholesterol panel values.  Supports .pdf, .jpg/.jpeg,
.png, and .webp inputs.

Returns a dict with float-or-None values for:
    total_cholesterol, hdl, ldl, apob   (all mg/dL)

Raises:
    ValueError        — unsupported file type
    anthropic.*Error  — API / auth failure
    json.JSONDecodeError — model returned non-JSON (shouldn't happen)
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Dict, Optional

import anthropic

_MODEL = "claude-haiku-4-5-20251001"

_PROMPT = """\
Extract the cholesterol panel values from this lab report.
Return ONLY a JSON object — no explanation, no markdown fences:

{
  "total_cholesterol": <mg/dL as number, or null>,
  "hdl":               <mg/dL as number, or null>,
  "ldl":               <mg/dL as number, or null>,
  "apob":              <mg/dL as number, or null>
}

Unit conversion rules if needed:
  cholesterol/HDL/LDL in mmol/L → multiply by 38.67
  ApoB in g/L → multiply by 100
"""

_IMAGE_MIME: Dict[str, str] = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".webp": "image/webp",
}


def extract_lab_values(file_path: str) -> Dict[str, Optional[float]]:
    """
    Call Claude to extract TC/HDL/LDL/ApoB from a dropped lab file.
    Returns dict with float or None for each key.
    """
    path = Path(file_path)
    ext  = path.suffix.lower()
    b64  = base64.standard_b64encode(path.read_bytes()).decode()

    if ext == ".pdf":
        content_block: dict = {
            "type": "document",
            "source": {
                "type":       "base64",
                "media_type": "application/pdf",
                "data":       b64,
            },
        }
    elif ext in _IMAGE_MIME:
        content_block = {
            "type": "image",
            "source": {
                "type":       "base64",
                "media_type": _IMAGE_MIME[ext],
                "data":       b64,
            },
        }
    else:
        raise ValueError(
            f"Unsupported file type '{ext}'. Drop a PDF, JPG, PNG, or WEBP."
        )

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=_MODEL,
        max_tokens=256,
        messages=[{
            "role": "user",
            "content": [
                content_block,
                {"type": "text", "text": _PROMPT},
            ],
        }],
    )

    raw = msg.content[0].text.strip()
    # Strip accidental markdown fences
    raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw, flags=re.MULTILINE)
    raw = raw.replace("```", "").strip()

    parsed = json.loads(raw)
    return {
        k: float(v) if v is not None else None
        for k, v in parsed.items()
    }
