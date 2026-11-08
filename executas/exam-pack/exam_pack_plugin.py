
"""AI-powered Exam Pack plugin for Anna App."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from executa_sdk import PROTOCOL_VERSION_V2, SamplingClient, SamplingError


MANIFEST = {
    "display_name": "Exam Pack",
    "version": "0.1.1",
    "description": "Creates study summaries, practice questions, and revision plans.",
    "author": "Exam Pack",
    "host_capabilities": ["llm.sample"],
    "tools": [
        {
            "name": "generate_exam_pack",
            "description": "Generate a study pack from student notes.",
            "parameters": [
                {
                    "name": "notes",
                    "type": "string",
                    "description": "The student's chapter notes or study material.",
                    "required": True,
                },
                {
                    "name": "subject",
                    "type": "string",
                    "description": "Subject name, if known.",
                    "required": False,
                    "default": "",
                },
                {
                    "name": "student_level",
                    "type": "string",
                    "description": "Student level or preferred explanation level.",
                    "required": False,
                    "default": "beginner-friendly",
                },
            ],
        }
    ],
    "runtime": {"type": "uv", "min_version": "0.1.0"},
}


_stdout_lock = threading.Lock()


def _write_frame(message: dict) -> None:
    with _stdout_lock:
        sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        sys.stdout.flush()


sampling = SamplingClient(write_frame=_write_frame)


def _make_response(req_id, *, result=None, error=None):
    response = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        response["error"] = error
    else:
        response["result"] = result
    return response


def _extract_text(result: dict) -> str:
    content = result.get("content") or {}
    if isinstance(content, dict):
        return content.get("text", "")
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return ""


async def _generate_exam_pack(
    notes: str,
    subject: str = "",
    student_level: str = "beginner-friendly",
    *,
    invoke_id: str,
) -> dict:
    if not notes or not notes.strip():
        raise ValueError("Please provide your study notes first.")

    system_prompt = """
You are Exam Pack, a helpful study assistant.
Use only the supplied study material for subject-specific facts.
If information is missing, say so rather than inventing facts.
Use clear, simple language suitable for the student's level.

Create exactly these three sections:

1. CHAPTER SUMMARY
Explain the important concepts in clear, concise bullet points.
Include key definitions and important terms.

2. 10 PRACTICE QUESTIONS WITH ANSWERS
Create exactly 10 useful questions based on the notes.
Include a clear answer after each question.
Mix short-answer and understanding-based questions.

3. 3-DAY REVISION PLAN
Create a practical plan for Day 1, Day 2, and Day 3.
Include what to revise and what to practise each day.

Use readable headings and formatting.
Do not add an introduction unrelated to the study material.
"""

    user_prompt = (
        f"Subject: {subject or 'Not specified'}\n"
        f"Student level: {student_level}\n\n"
        f"Study material:\n{notes}"
    )

    result = await sampling.create_message(
        messages=[
            {
                "role": "user",
                "content": {"type": "text", "text": user_prompt},
            }
        ],
        max_tokens=4000,
        system_prompt=system_prompt,
        temperature=0.4,
        metadata={
            "executa_invoke_id": invoke_id,
            "tool": "generate_exam_pack",
        },
        timeout=120.0,
    )

    return {
        "text": _extract_text(result),
        "model": result.get("model"),
        "usage": result.get("usage"),
    }


def _handle_initialize(req_id, params):
    protocol = (params or {}).get("protocolVersion") or "1.1"

    if protocol != PROTOCOL_VERSION_V2:
        sampling.disable(
            "Exam Pack requires Executa protocol 2.0 for AI sampling."
        )

    return _make_response(
        req_id,
        result={
            "protocolVersion": protocol,
            "serverInfo": {
                "name": MANIFEST["display_name"],
                "version": MANIFEST["version"],
            },
            "client_capabilities": {"sampling": {}}
            if protocol == PROTOCOL_VERSION_V2 else {},
            "capabilities": {},
        },
    )


def _handle_invoke(req_id, params):
    tool = params.get("tool")
    arguments = params.get("arguments") or {}
    invoke_id = params.get("invoke_id") or ""

    if tool != "generate_exam_pack":
        return _make_response(
            req_id,
            error={"code": -32601, "message": f"Unknown tool: {tool}"},
        )

    future = asyncio.run_coroutine_threadsafe(
        _generate_exam_pack(invoke_id=invoke_id, **arguments),
        _loop,
    )

    try:
        data = future.result(timeout=150)
        return _make_response(
            req_id,
            result={
                "success": True,
                "tool": tool,
                "data": data,
            },
        )
    except SamplingError as error:
        return _make_response(
            req_id,
            error={"code": error.code, "message": error.message},
        )
    except (TypeError, ValueError) as error:
        return _make_response(
            req_id,
            error={"code": -32602, "message": str(error)},
        )
    except Exception as error:
        return _make_response(
            req_id,
            error={"code": -32603, "message": str(error)},
        )


_loop = asyncio.new_event_loop()
_loop_thread = threading.Thread(target=_loop.run_forever, daemon=True)
_loop_thread.start()


def _handle_message(line: str) -> None:
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        _write_frame(
            _make_response(
                None,
                error={"code": -32700, "message": "Parse error"},
            )
        )
        return

    # Replies from Anna's host resolve pending AI sampling requests.
    if "method" not in message:
        sampling.dispatch_response(message)
        return

    method = message.get("method")
    req_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        response = _handle_initialize(req_id, params)
    elif method == "describe":
        response = _make_response(req_id, result=MANIFEST)
    elif method == "health":
        response = _make_response(
            req_id,
            result={
                "status": "healthy",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
    elif method == "invoke":
        response = _handle_invoke(req_id, params)
    elif method == "shutdown":
        response = _make_response(req_id, result={"ok": True})
    else:
        response = _make_response(
            req_id,
            error={"code": -32601, "message": f"Unknown method: {method}"},
        )

    if req_id is not None:
        _write_frame(response)


def main() -> None:
    pool = ThreadPoolExecutor(max_workers=4)
    try:
        for raw in sys.stdin:
            line = raw.strip()
            if line:
                pool.submit(_handle_message, line)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        _loop.call_soon_threadsafe(_loop.stop)


if __name__ == "__main__":
    main()
