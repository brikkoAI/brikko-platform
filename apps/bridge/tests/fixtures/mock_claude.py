"""Mock claude.exe for tests — emits a deterministic stream-json sequence
that mirrors a real Glob+Read happy-path session."""
import json
import sys
import time

EVENTS = [
    {"type": "system", "subtype": "init",
     "session_id": "test-session-id",
     "cwd": "/tmp/x",
     "model": "claude-sonnet-4-test",
     "tools": ["Read", "Glob", "Edit", "Bash"]},
    {"type": "rate_limit_event", "remaining": 100},
    {"type": "assistant",
     "message": {"content": [{"type": "text", "text": "Looking at the files... "}]}},
    {"type": "assistant",
     "message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/x/test.txt"}}
     ]}},
    {"type": "user",
     "message": {"content": [
        {"type": "tool_result", "is_error": False, "content": "1\thello\n2\tworld\n"}
     ]}},
    {"type": "assistant",
     "message": {"content": [{"type": "text", "text": "Found 2 lines."}]}},
    {"type": "result", "subtype": "success",
     "is_error": False,
     "duration_ms": 1234,
     "num_turns": 2,
     "total_cost_usd": 0.001,
     "result": "Found 2 lines.",
     "usage": {"input_tokens": 50, "output_tokens": 10}},
]

if __name__ == "__main__":
    # Validate args expected by run_claude
    assert "--print" in sys.argv, f"missing --print: {sys.argv}"
    assert "--output-format=stream-json" in sys.argv
    assert "--dangerously-skip-permissions" in sys.argv

    for ev in EVENTS:
        print(json.dumps(ev), flush=True)
        time.sleep(0.01)  # tiny delay to simulate real streaming
