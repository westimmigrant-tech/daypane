#!/usr/bin/env python3
"""Read Codex account quotas over stdio and publish a minimal GitHub snapshot."""

import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.request


WINDOWS = {300: "fiveHour", 10080: "weekly"}
TOKEN_FILE = Path.home() / ".config/daypane/github-token"
REPORT_URL = "https://api.github.com/repos/westimmigrant-tech/daypane/contents/quotas.json"
BRANCH = "runtime-data"


def summarize(result, now):
    buckets = result.get("rateLimitsByLimitId") or {}
    bucket = buckets.get("codex")
    if bucket is None:
        bucket = result.get("rateLimits")
        if not isinstance(bucket, dict) or bucket.get("limitId") not in (None, "codex"):
            raise ValueError("Codex quota bucket unavailable")

    windows = {}
    for key in ("primary", "secondary"):
        window = bucket.get(key) or {}
        duration = window.get("windowDurationMins")
        name = WINDOWS.get(duration)
        if name is None:
            continue
        used, reset = window.get("usedPercent"), window.get("resetsAt")
        if (name in windows or type(used) not in (int, float) or not 0 <= used <= 100
                or type(reset) is not int or not 0 < reset <= now + duration * 60 + 300):
            raise ValueError("invalid Codex quota window")
        windows[name] = {"usedPercent": used, "resetsAt": reset}
    if set(windows) != set(WINDOWS.values()):
        raise ValueError("expected both the five-hour and weekly Codex windows")
    return {"ts": int(now * 1000), "quotas": {"codex": {"ok": True, "windows": windows}}}


async def exchange(process):
    async def send(message):
        process.stdin.write(json.dumps(message).encode() + b"\n")
        await process.stdin.drain()

    async def receive(request_id):
        while line := await process.stdout.readline():
            message = json.loads(line)
            if message.get("id") != request_id or "method" in message:
                continue
            if "result" in message:
                return message["result"]
            raise ValueError("Codex app-server rejected the request")
        raise ValueError("Codex app-server closed the connection")

    await send({"id": 1, "method": "initialize",
                "params": {"clientInfo": {"name": "daypane_quota_reporter", "version": "1.0.0"}}})
    await receive(1)
    await send({"method": "initialized", "params": {}})
    await send({"id": 2, "method": "account/rateLimits/read"})
    return await receive(2)


async def collect(codex):
    # Use the user's existing Codex login. The reporter never opens its auth store.
    with tempfile.TemporaryDirectory(prefix="daypane-codex-") as cwd:
        process = await asyncio.create_subprocess_exec(
            codex, "app-server", "--disable", "plugins", "--listen", "stdio://",
            cwd=cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        try:
            result = await asyncio.wait_for(exchange(process), timeout=30)
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.communicate()
    return summarize(result, time.time())


def read_token():
    with os.fdopen(os.open(TOKEN_FILE, os.O_RDONLY | os.O_NOFOLLOW)) as f:
        info = os.fstat(f.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            raise PermissionError("GitHub token file must be private to the current user")
        token = f.read(1024).strip()
    if not token.startswith("github_pat_") or not token.isascii() or not token.replace("_", "").isalnum():
        raise ValueError("expected a GitHub fine-grained token")
    return token


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def publish(report):
    token = read_token()
    opener = urllib.request.build_opener(NoRedirect())
    headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
               "User-Agent": "DayPane-Codex-Quota", "X-GitHub-Api-Version": "2022-11-28"}

    def request(url, body=None):
        request_headers = headers.copy()
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, headers=request_headers,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     method="PUT" if body is not None else "GET")
        with opener.open(req, timeout=15) as response:
            return json.load(response)

    try:
        previous = request(REPORT_URL + "?ref=" + BRANCH)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        previous = {}
    if previous:
        old = json.loads(base64.b64decode(previous["content"]))
        if isinstance(old, dict) and type(old.get("ts")) is int:
            age_ms = report["ts"] - old["ts"]
            if age_ms <= 0 or (age_ms < 10 * 60 * 1000 and old.get("quotas") == report["quotas"]):
                return
    body = {"message": "Update Codex quota summary", "branch": BRANCH,
            "content": base64.b64encode((json.dumps(report, indent=2) + "\n").encode()).decode()}
    if previous:
        body["sha"] = previous["sha"]
    request(REPORT_URL, body)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default="codex", help="Codex CLI executable")
    parser.add_argument("--publish", action="store_true", help="upload to runtime-data/quotas.json")
    args = parser.parse_args()
    try:
        report = asyncio.run(collect(args.codex))
    except Exception:
        print("DayPane: could not read both Codex quota windows.", file=sys.stderr)
        return 1
    if not args.publish:
        print(json.dumps(report, indent=2))
        return 0
    try:
        publish(report)
    except urllib.error.HTTPError as e:
        print(f"DayPane: GitHub quota upload failed (HTTP {e.code}).", file=sys.stderr)
        return 1
    except Exception:
        print("DayPane: quota upload failed; check the network and private GitHub token file.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
