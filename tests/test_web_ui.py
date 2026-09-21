import asyncio
import subprocess
import unittest
from pathlib import Path

from starlette.requests import Request

from web_ui import server


class WebUILogTests(unittest.TestCase):
    def tearDown(self) -> None:
        server.jobs.pop("test-job", None)
        server.job_logs.pop("test-job", None)

    def test_log_stream_resumes_after_last_event_id(self) -> None:
        server.jobs["test-job"] = {"status": "completed"}
        server.job_logs["test-job"] = ["one\n", "two\n", "three\n"]
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/jobs/test-job/logs",
                "headers": [(b"last-event-id", b"2")],
            }
        )

        async def read_stream() -> str:
            response = await server.get_job_logs_stream("test-job", request)
            chunks = [chunk async for chunk in response.body_iterator]
            return "".join(chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks)

        output = asyncio.run(read_stream())

        self.assertNotIn("data: one", output)
        self.assertNotIn("data: two", output)
        self.assertIn("id: 3\ndata: three", output)

    def test_terminal_keeps_only_the_latest_thousand_lines(self) -> None:
        app_js = Path(__file__).parents[1] / "web_ui" / "static" / "app.js"
        script = r"""
const fs = require('fs');
const vm = require('vm');
const children = [];
const terminal = {
  appendChild(node) { children.push(node); },
  get childElementCount() { return children.length; },
  get firstElementChild() {
    return children.length ? { remove() { children.shift(); } } : null;
  },
  get lastElementChild() { return children.at(-1); },
  scrollTop: 0,
  scrollHeight: 0,
};
const document = {
  addEventListener() {},
  getElementById(id) { return id === 'terminal-console' ? terminal : null; },
  createElement() {
    const classes = new Set();
    return {
      className: '',
      classList: {
        add(name) { classes.add(name); },
        contains(name) { return classes.has(name); },
      },
      textContent: '',
    };
  },
};
const context = { document, console, JSON };
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
for (let i = 0; i < 5000; i++) context.appendLog(`line ${i}`);
if (children.length !== 1000) throw new Error(`retained ${children.length} terminal lines`);
"""

        subprocess.run(["node", "-e", script, str(app_js)], check=True)


if __name__ == "__main__":
    unittest.main()
