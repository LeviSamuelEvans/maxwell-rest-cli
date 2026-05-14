#!/usr/bin/env python3
# noqa: EXE001

import curses
import json
import locale
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[1]

MAXWELL_BIN = Path(os.environ.get("MAXWELL_BIN", ROOT / "bin" / "maxwell"))

REFRESH_SECONDS = 30

COLORS: dict[str, int] = {}
JsonDict = dict[str, Any]


def config_dir() -> Path:
    if value := os.environ.get("MAXWELL_CONFIG_DIR"):
        return Path(value).expanduser()

    if value := os.environ.get("XDG_CONFIG_HOME"):
        return Path(value).expanduser() / "maxwell-rest"

    return Path.home() / ".config" / "maxwell-rest"


def config_file() -> Path:
    if value := os.environ.get("MAXWELL_CONFIG_FILE"):
        return Path(value).expanduser()
    return config_dir() / "config.env"


def cache_file() -> Path:
    if value := os.environ.get("MAXWELL_CACHE_FILE"):
        return Path(value).expanduser()
    return config_dir() / "cache.env"


def read_env_file(path: Path) -> dict[str, str]:
    """reads the environment file at the given path and returns a dictionary of the environment variables"""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        try:
            parts = shlex.split(raw_value)
        except ValueError:
            parts = [raw_value]
        values[key] = parts[0] if parts else ""
    return values


def first_scalar(value: Any, default: str = "-") -> str:
    """returns the first scalar value from the given value"""
    if isinstance(value, list):
        return first_scalar(value[0], default) if value else default
    if value is None:
        return default
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def numeric_value(value: Any, default: str = "-") -> str:
    if isinstance(value, dict):
        if value.get("infinite"):
            return "infinite"
        if "number" in value:
            return first_scalar(value.get("number"), default)
    return first_scalar(value, default)


def epoch_time(value: Any) -> str:
    raw = numeric_value(value)
    if raw == "-":
        return "-"
    try:
        number = int(float(raw))
    except ValueError:
        return raw
    if number <= 0:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(number))


def tres_list(value: Any) -> str:
    if not isinstance(value, list):
        return first_scalar(value)
    parts = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("type")
        count = item.get("count")
        if name and count is not None:
            parts.append(f"{name}={count}")
    return ",".join(parts) if parts else "-"


def pick(job: JsonDict, *keys: str) -> str:
    for key in keys:
        if key in job:
            return first_scalar(job[key])
    return "-"


def job_id(job: JsonDict) -> str:
    return pick(job, "job_id", "id")


def job_tres(job: JsonDict, string_key: str, nested_key: str) -> str:
    if value := job.get(string_key):
        return first_scalar(value)
    tres = job.get("tres")
    if isinstance(tres, dict):
        return tres_list(tres.get(nested_key))
    return "-"


def field_lines(fields: list[tuple[str, str]]) -> list[str]:
    return [f"{key}: {value}" for key, value in fields if value != "-"]


@dataclass(frozen=True)
class JobView:
    id: str
    state: str
    partition: str
    name: str
    user: str
    time: str
    nodes: str = "-"
    submit_time: str = "-"
    start_time: str = "-"
    end_time: str = "-"

    @classmethod
    def from_api(cls, job: JsonDict) -> "JobView":
        return cls(
            id=job_id(job),
            state=pick(job, "job_state", "state"),
            partition=pick(job, "partition"),
            name=pick(job, "name", "job_name"),
            user=pick(job, "user_name", "user"),
            time=pick(job, "time_used", "run_time", "time_limit"),
            nodes=first_scalar(job.get("nodes")),
            submit_time=first_scalar(job.get("submit_time")),
            start_time=first_scalar(job.get("start_time")),
            end_time=first_scalar(job.get("end_time")),
        )

    def table_values(self) -> list[str]:
        return [self.id, self.state, self.partition, self.name, self.user, self.time]

    def summary_lines(self) -> list[str]:
        return field_lines(
            [
                ("job_id", self.id),
                ("state", self.state),
                ("partition", self.partition),
                ("name", self.name),
                ("user", self.user),
                ("time", self.time),
                ("nodes", self.nodes),
                ("submit_time", self.submit_time),
                ("start_time", self.start_time),
                ("end_time", self.end_time),
            ]
        )


def response_job(payload: JsonDict) -> JsonDict:
    job = payload.get("jobs", [{}])
    if isinstance(job, list):
        return job[0] if job and isinstance(job[0], dict) else {}
    if isinstance(job, dict):
        return job
    direct = payload.get("job", payload)
    return direct if isinstance(direct, dict) else {}


def state_reason(job: JsonDict) -> str:
    state = job.get("state")
    if isinstance(state, dict):
        return first_scalar(state.get("reason"))
    return first_scalar(job.get("state_reason"))


def exit_status(job: JsonDict) -> str:
    exit_code = job.get("exit_code")
    if isinstance(exit_code, dict):
        status = exit_code.get("status")
        return first_scalar(status)
    return "-"


def stdout_path(job: JsonDict) -> str:
    return first_scalar(
        job.get("stdout_expanded", job.get("standard_output", job.get("stdout")))
    )


def requested_tres(job: JsonDict) -> str:
    return job_tres(job, "tres_req_str", "requested")


def allocated_tres(job: JsonDict) -> str:
    return job_tres(job, "tres_alloc_str", "allocated")


def summary_lines(payload: JsonDict, source: str) -> list[str]:
    job = response_job(payload)
    if not job:
        return ["No job data returned."]
    if source == "history":
        state_data = job.get("state")
        time_data = job.get("time") if isinstance(job.get("time"), dict) else {}
        state = first_scalar(
            state_data.get("current") if isinstance(state_data, dict) else state_data
        )
        submit_time = epoch_time(time_data.get("submission"))
        start_time = epoch_time(time_data.get("start"))
        end_time = epoch_time(time_data.get("end"))
        elapsed = first_scalar(time_data.get("elapsed"))
        nodes = first_scalar(job.get("nodes"))
    else:
        state = pick(job, "job_state", "state")
        submit_time = epoch_time(job.get("submit_time"))
        start_time = epoch_time(job.get("start_time"))
        end_time = epoch_time(job.get("end_time"))
        elapsed = pick(job, "time_used", "run_time", "time_limit")
        nodes = first_scalar(job.get("nodes"))
    fields = [
        ("job_id", job_id(job)),
        ("name", pick(job, "name", "job_name")),
        ("state", state),
        ("reason", state_reason(job)),
        ("partition", pick(job, "partition")),
        ("nodes", nodes),
        ("submit", submit_time),
        ("start", start_time),
        ("end", end_time),
        ("elapsed", elapsed),
        ("requested", requested_tres(job)),
        ("allocated", allocated_tres(job)),
        ("stdout", stdout_path(job)),
        ("exit", exit_status(job)),
    ]
    return field_lines(fields)


@dataclass
class CommandResult:
    """represents the result of a command execution"""
    ok: bool
    output: str
    error: str = ""


@dataclass(frozen=True)
class SubmitRequest:
    script: str
    name: str
    partition: str
    time_limit: str
    cpus: str
    tasks: str
    mem: str


class MaxwellAPI(Protocol):
    def jobs(self, user: str) -> tuple[list[JsonDict], str | None]: ...

    def detail_summary(self, command: str, jid: str) -> tuple[str, list[str]]: ...

    def cancel(self, jid: str) -> str: ...

    def submit(self, request: SubmitRequest) -> str: ...


@dataclass
class AppState:
    """represents the state of the application"""
    jobs: list[JobView] = field(default_factory=list)
    selected: int = 0
    status: str = "Press r to refresh"
    detail_title: str = "Details"
    detail_lines: list[str] = field(default_factory=list)
    next_refresh: float = 0
    running: bool = True
    filter_user: str = ""

    def selected_job(self) -> JobView | None:
        if not self.jobs:
            return None
        return self.jobs[self.selected]

    def selected_job_id(self) -> str | None:
        job = self.selected_job()
        return job.id if job else None

    def show_selected_summary(self) -> None:
        job = self.selected_job()
        if not job:
            self.detail_title = "Details"
            self.detail_lines = ["No jobs for current filter."]
            return
        self.detail_title = f"Selected {job.id}"
        self.detail_lines = job.summary_lines()

    def replace_jobs(self, jobs: list[JsonDict]) -> None:
        self.jobs = [JobView.from_api(job) for job in jobs]
        if self.selected >= len(self.jobs):
            self.selected = max(0, len(self.jobs) - 1)
        self.show_selected_summary()

    def select(self, delta: int) -> None:
        if not self.jobs:
            return
        self.selected = max(0, min(len(self.jobs) - 1, self.selected + delta))
        self.show_selected_summary()


class MaxwellClient:
    """client for the Maxwell REST API"""
    def run(self, *args: str) -> CommandResult:
        cmd = [str(MAXWELL_BIN), *args]
        try:
            proc = subprocess.run(
                cmd,
                text=True,
                capture_output=True,
                check=False,
                timeout=60,
            )
        except FileNotFoundError:
            return CommandResult(False, "", f"missing maxwell binary: {MAXWELL_BIN}")
        except subprocess.TimeoutExpired:
            return CommandResult(False, "", "command timed out")
        if proc.returncode != 0:
            return CommandResult(
                False, proc.stdout, proc.stderr.strip() or proc.stdout.strip()
            )
        return CommandResult(True, proc.stdout, proc.stderr)

    def jobs(self, user: str) -> tuple[list[dict[str, Any]], str | None]:
        args = ["jobs", "--json"]
        if user:
            args += ["--user", user]
        result = self.run(*args)
        if not result.ok:
            return [], result.error
        try:
            payload = json.loads(result.output)
        except json.JSONDecodeError as exc:
            return [], f"invalid jobs JSON: {exc}"
        jobs = payload.get("jobs", [])
        if not isinstance(jobs, list):
            return [], "jobs response did not contain a jobs list"
        return [job for job in jobs if isinstance(job, dict)], None

    def detail_summary(self, command: str, jid: str) -> tuple[str, list[str]]:
        result = self.run(command, jid, "--json")
        if not result.ok:
            return command, [result.error]
        try:
            payload = json.loads(result.output)
        except json.JSONDecodeError as exc:
            return command, [f"invalid {command} JSON: {exc}"]
        return f"{command} {jid}", summary_lines(payload, command)

    def cancel(self, jid: str) -> str:
        result = self.run("cancel", jid)
        if result.ok:
            return result.output.strip() or f"Cancel request sent for job {jid}"
        return result.error

    def submit(self, request: SubmitRequest) -> str:
        args = [
            "submit",
            request.script,
            "--name",
            request.name,
            "--partition",
            request.partition,
            "--time",
            request.time_limit,
            "--cpus",
            request.cpus,
            "--tasks",
            request.tasks,
            "--mem",
            request.mem,
            "--json",
        ]
        result = self.run(*args)
        if not result.ok:
            return result.error
        try:
            payload = json.loads(result.output)
            jid = payload.get("job_id") or payload.get("result", {}).get("job_id")
            return f"Submitted job {jid}" if jid else "Submit returned no job id"
        except json.JSONDecodeError:
            return result.output.strip()


class SelfTestClient:
    """client for the Maxwell REST API for self-testing"""
    def __init__(self) -> None:
        self.refreshes = 0

    def jobs(self, user: str) -> tuple[list[dict[str, Any]], str | None]:
        self.refreshes += 1
        return [
            {
                "job_id": 1,
                "job_state": "RUNNING",
                "partition": "allcpu",
                "name": "one",
                "user_name": user,
            },
            {
                "job_id": 2,
                "job_state": "PENDING",
                "partition": "allcpu",
                "name": "two",
                "user_name": user,
            },
        ], None

    def detail_summary(self, command: str, jid: str) -> tuple[str, list[str]]:
        payload = {
            "jobs": [
                {
                    "job_id": int(jid),
                    "name": "one",
                    "job_state": ["RUNNING"],
                    "state_reason": "None",
                    "partition": "allcpu",
                    "nodes": "max-wn001",
                    "submit_time": {"set": True, "number": 1778116305},
                    "start_time": {"set": True, "number": 1778116312},
                    "tres_req_str": "cpu=1,mem=1000M,node=1,billing=1",
                    "tres_alloc_str": "cpu=40,node=1,billing=40",
                    "stdout_expanded": "/home/alice/slurm-1.out",
                    "exit_code": {"status": ["SUCCESS"]},
                }
            ]
        }
        if command == "history":
            payload["jobs"][0]["state"] = {"current": ["COMPLETED"], "reason": "None"}
            payload["jobs"][0]["time"] = {
                "submission": 1778116305,
                "start": 1778116312,
                "end": 1778116342,
                "elapsed": 30,
            }
            payload["jobs"][0]["tres"] = {
                "requested": [
                    {"type": "cpu", "count": 1},
                    {"type": "mem", "count": 1000},
                    {"type": "billing", "count": 1},
                ],
                "allocated": [
                    {"type": "cpu", "count": 40},
                    {"type": "billing", "count": 40},
                ],
            }
        return f"{command} {jid}", summary_lines(payload, command)

    def cancel(self, jid: str) -> str:
        return f"Cancel request sent for job {jid}"

    def submit(self, request: SubmitRequest) -> str:
        return "Submitted job 3"


def token_status() -> str:
    cache = read_env_file(cache_file())
    raw = cache.get("MAXWELL_SLURM_TOKEN_EXPIRES_AT")
    if not raw:
        return "token expiry unknown"
    try:
        remaining = int(raw) - int(time.time())
    except ValueError:
        return "token expiry invalid"
    if remaining <= 0:
        return "token expired"
    hours, rem = divmod(remaining, 3600)
    minutes = rem // 60
    return f"token valid {hours}h {minutes}m"


def ellipsize(text: str, width: int) -> str:
    """truncate text to the given width and replace the last character with a '>'"""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return text[:1]
    return text[: width - 1] + ">"


def addstr(win: curses.window, y: int, x: int, text: str, attr: int = 0) -> None:
    """adds the given text to the given window at the given coordinates with the given attributes"""
    height, width = win.getmaxyx()
    if y < 0 or y >= height or width <= 0:
        return
    if x >= width:
        return

    # be defensive around terminal/curses edge cases:
    if x < 0:
        text = text[-x:]
        x = 0

    max_width = width - x
    if y == height - 1:
        # avoid touching the bottom-right cell (last row, last col).
        max_width -= 1
    if max_width <= 0:
        return

    try:
        win.addstr(y, x, ellipsize(text, max_width), attr)
    except curses.error:
        # rendering should never crash the TUI so just skip if the terminal is too small or
        # the curses backend refuses the write for any reason.
        return


def init_colors() -> None:
    COLORS.clear()
    if not curses.has_colors():
        COLORS.update(
            title=curses.A_BOLD,
            help=0,
            header=curses.A_BOLD,
            selected=curses.A_REVERSE,
            divider=curses.A_BOLD,
            detail_title=curses.A_BOLD,
            status=curses.A_REVERSE,
        )
        return

    curses.start_color()
    try:
        curses.use_default_colors()
        background = -1
    except curses.error:
        background = curses.COLOR_BLACK

    pairs = {
        "title": (curses.COLOR_CYAN, background),
        "help": (curses.COLOR_WHITE, background),
        "header": (curses.COLOR_YELLOW, background),
        "selected": (curses.COLOR_BLACK, curses.COLOR_CYAN),
        "divider": (curses.COLOR_WHITE, background),
        "detail_title": (curses.COLOR_GREEN, background),
        "status": (curses.COLOR_BLACK, curses.COLOR_WHITE),
    }
    for index, (name, (fg, bg)) in enumerate(pairs.items(), start=1):
        try:
            curses.init_pair(index, fg, bg)
            COLORS[name] = curses.color_pair(index)
        except curses.error:
            COLORS[name] = 0

    COLORS["title"] |= curses.A_BOLD
    COLORS["header"] |= curses.A_BOLD
    COLORS["divider"] |= curses.A_BOLD
    COLORS["detail_title"] |= curses.A_BOLD


def style(name: str, fallback: int = 0) -> int:
    return COLORS.get(name, fallback)


def load_user() -> str:
    return read_env_file(config_file()).get("MAXWELL_USER", os.environ.get("USER", ""))


def refresh_jobs(state: AppState, client: MaxwellAPI) -> None:
    state.status = "Refreshing jobs..."
    jobs, error = client.jobs(state.filter_user)
    state.next_refresh = time.time() + REFRESH_SECONDS
    if error:
        state.status = error
        return
    state.replace_jobs(jobs)
    state.status = f"{len(state.jobs)} jobs loaded"


def draw_header(stdscr: curses.window, state: AppState) -> None:
    height, width = stdscr.getmaxyx()
    title = "Maxwell Jobs"
    filter_text = f"user {state.filter_user or 'all'}"
    right = f"{filter_text} | {token_status()}"
    addstr(stdscr, 0, 0, title, style("title", curses.A_BOLD))
    addstr(stdscr, 0, max(0, width - len(right) - 1), right, style("help"))
    addstr(
        stdscr,
        1,
        0,
        "r refresh  s submit  Enter details  h history  c cancel  q quit",
        style("help"),
    )
    addstr(stdscr, height - 1, 0, state.status, style("status", curses.A_REVERSE))


def draw_jobs(
    stdscr: curses.window, state: AppState, top: int, left: int, height: int, width: int
) -> None:
    headers = [
        ("JOBID", 10),
        ("STATE", 12),
        ("PARTITION", 12),
        ("NAME", 24),
        ("USER", 12),
        ("TIME", 10),
    ]
    x = left
    for label, col_width in headers:
        addstr(stdscr, top, x, label.ljust(col_width), style("header", curses.A_BOLD))
        x += col_width + 1
    visible_rows = max(0, height - 1)
    start = 0
    if state.selected >= visible_rows:
        start = state.selected - visible_rows + 1
    for row, job in enumerate(state.jobs[start : start + visible_rows], start=top + 1):
        index = start + row - top - 1
        attr = style("selected", curses.A_REVERSE) if index == state.selected else 0
        x = left
        for value, (_, col_width) in zip(job.table_values(), headers):
            addstr(stdscr, row, x, value.ljust(col_width), attr)
            x += col_width + 1
            if x >= left + width:
                break


def draw_details(
    stdscr: curses.window, state: AppState, top: int, left: int, height: int, width: int
) -> None:
    addstr(stdscr, top, left, state.detail_title, style("detail_title", curses.A_BOLD))
    for offset, line in enumerate(state.detail_lines[: max(0, height - 1)], start=1):
        addstr(stdscr, top + offset, left, line)


def draw(stdscr: curses.window, state: AppState) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    draw_header(stdscr, state)
    body_top = 3
    body_height = max(0, height - body_top - 2)
    if width >= 110:
        jobs_width = int(width * 0.62)
        draw_jobs(stdscr, state, body_top, 0, body_height, jobs_width)
        for y in range(body_top, body_top + body_height):
            addstr(stdscr, y, jobs_width, "|", style("divider", curses.A_BOLD))
        draw_details(
            stdscr, state, body_top, jobs_width + 2, body_height, width - jobs_width - 2
        )
    else:
        split = max(8, body_height // 2)
        draw_jobs(stdscr, state, body_top, 0, split, width)
        draw_details(
            stdscr, state, body_top + split + 1, 0, body_height - split - 1, width
        )
    stdscr.refresh()


def prompt_confirm(stdscr: curses.window, message: str) -> bool:
    height, _ = stdscr.getmaxyx()
    addstr(stdscr, height - 1, 0, f"{message} [y/N]", style("status", curses.A_REVERSE))
    stdscr.refresh()
    stdscr.timeout(-1)
    try:
        while True:
            key = read_key(stdscr)
            if key in ("y", "Y"):
                return True
            if key in ("n", "N", "\x1b", "\n", "\r", curses.KEY_ENTER):
                return False
    finally:
        stdscr.timeout(250)


def prompt_input(stdscr: curses.window, label: str, default: str = "") -> str | None:
    """Read a line of input from the status bar. Blocks until Enter is pressed."""
    height, width = stdscr.getmaxyx()
    prompt = f"{label} [{default}]: " if default else f"{label}: "
    addstr(stdscr, height - 1, 0, " " * max(0, width - 1), style("status", curses.A_REVERSE))
    addstr(stdscr, height - 1, 0, prompt, style("status", curses.A_REVERSE))
    stdscr.refresh()

    stdscr.timeout(-1)
    curses.echo()
    try:
        curses.curs_set(1)
    except curses.error:
        pass
    try:
        raw = stdscr.getstr(
            height - 1,
            min(len(prompt), width - 1),
            max(1, width - len(prompt) - 1),
        )
        value = raw.decode("utf-8").strip()
        return value or default
    except (curses.error, UnicodeDecodeError):
        return None
    finally:
        curses.noecho()
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        stdscr.timeout(250)


def submit_flow(stdscr: curses.window, state: AppState, client: MaxwellAPI) -> None:
    script = prompt_input(stdscr, "Script path")
    if not script:
        state.status = "Submit aborted"
        return
    prompts = [
        ("Name", Path(script).name or "maxwell-job"),
        ("Partition", "allcpu"),
        ("Time seconds", "300"),
        ("CPUs per task", "1"),
        ("Tasks", "1"),
        ("Memory MB", "1000"),
    ]
    values: list[str] = []
    for label, default in prompts:
        value = prompt_input(stdscr, label, default)
        if not value:
            state.status = "Submit aborted"
            return
        values.append(value)
    name, partition, time_limit, cpus, tasks, mem = values
    summary = f"Submit {name} on {partition}, {tasks} task(s), {cpus} CPU/task, {mem} MB, {time_limit}s?"
    if not prompt_confirm(stdscr, summary):
        state.status = "Submit aborted"
        return
    request = SubmitRequest(script, name, partition, time_limit, cpus, tasks, mem)
    state.status = client.submit(request)
    refresh_jobs(state, client)


def read_key(stdscr: curses.window) -> str | int | None:
    try:
        return stdscr.get_wch()
    except curses.error:
        return None


def handle_key(
    stdscr: curses.window | None, state: AppState, client: MaxwellAPI, key: str | int
) -> None:
    k = key.lower() if isinstance(key, str) else key
    if k == "q":
        state.running = False
    elif k == "r":
        refresh_jobs(state, client)
    elif k in (curses.KEY_DOWN, "j"):
        state.select(+1)
    elif k in (curses.KEY_UP, "k"):
        state.select(-1)
    elif k in (curses.KEY_ENTER, "\n", "\r"):
        if jid := state.selected_job_id():
            state.detail_title, state.detail_lines = client.detail_summary("job", jid)
            state.status = f"Loaded job {jid}"
    elif k == "h":
        if jid := state.selected_job_id():
            state.detail_title, state.detail_lines = client.detail_summary("history", jid)
            state.status = f"Loaded history {jid}"
    elif k == "s":
        if stdscr is not None:
            submit_flow(stdscr, state, client)
    elif k == "c":
        jid = state.selected_job_id()
        if stdscr is not None and jid and prompt_confirm(stdscr, f"Cancel job {jid}?"):
            state.status = client.cancel(jid)
            refresh_jobs(state, client)
        elif state.jobs:
            state.status = "Cancel aborted"


def self_test_keys() -> None:
    client = SelfTestClient()
    state = AppState(filter_user="alice")
    refresh_jobs(state, client)
    assert len(state.jobs) == 2
    handle_key(None, state, client, "j")
    assert state.selected == 1
    handle_key(None, state, client, "k")
    assert state.selected == 0
    handle_key(None, state, client, "\n")
    assert state.detail_title == "job 1"
    assert "requested: cpu=1,mem=1000M,node=1,billing=1" in state.detail_lines
    assert "allocated: cpu=40,node=1,billing=40" in state.detail_lines
    assert "stdout: /home/alice/slurm-1.out" in state.detail_lines
    handle_key(None, state, client, "h")
    assert state.detail_title == "history 1"
    assert "state: COMPLETED" in state.detail_lines
    assert "requested: cpu=1,mem=1000M,node=1,billing=1" in state.detail_lines
    handle_key(None, state, client, "r")
    assert client.refreshes == 2
    handle_key(None, state, client, "q")
    assert not state.running


def main(stdscr: curses.window) -> int:
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    curses.noecho()
    curses.cbreak()
    init_colors()
    stdscr.keypad(True)
    stdscr.timeout(250)
    client = MaxwellClient()
    state = AppState(filter_user=load_user())
    refresh_jobs(state, client)
    while state.running:
        if time.time() >= state.next_refresh:
            refresh_jobs(state, client)
        draw(stdscr, state)
        key = read_key(stdscr)
        if key is not None:
            handle_key(stdscr, state, client, key)
    return 0


if __name__ == "__main__":
    if "--self-test-keys" in sys.argv:
        self_test_keys()
        print("keys=ok")
        raise SystemExit(0)
    if "--check" in sys.argv:
        user = load_user()
        jobs, error = MaxwellClient().jobs(user)
        if error:
            print(error, file=sys.stderr)
            raise SystemExit(1)
        print(f"jobs={len(jobs)} user={user}")
        raise SystemExit(0)
    try:
        raise SystemExit(curses.wrapper(main))
    except KeyboardInterrupt:
        raise SystemExit(130)
