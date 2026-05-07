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
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

MAXWELL_BIN = Path(os.environ.get("MAXWELL_BIN", ROOT / "bin" / "maxwell"))

REFRESH_SECONDS = 30

COLORS: dict[str, int] = {}


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


def job_id(job: dict[str, Any]) -> str:
    return first_scalar(job.get("job_id", job.get("id")))


def job_state(job: dict[str, Any]) -> str:
    return first_scalar(job.get("job_state", job.get("state")))


def job_name(job: dict[str, Any]) -> str:
    return first_scalar(job.get("name", job.get("job_name")))


def job_user(job: dict[str, Any]) -> str:
    return first_scalar(job.get("user_name", job.get("user")))


def job_partition(job: dict[str, Any]) -> str:
    return first_scalar(job.get("partition"))


def job_time(job: dict[str, Any]) -> str:
    return first_scalar(
        job.get("time_used", job.get("run_time", job.get("time_limit")))
    )


def response_job(payload: dict[str, Any]) -> dict[str, Any]:
    job = payload.get("jobs", [{}])
    if isinstance(job, list):
        return job[0] if job and isinstance(job[0], dict) else {}
    if isinstance(job, dict):
        return job
    direct = payload.get("job", payload)
    return direct if isinstance(direct, dict) else {}


def state_reason(job: dict[str, Any]) -> str:
    state = job.get("state")
    if isinstance(state, dict):
        return first_scalar(state.get("reason"))
    return first_scalar(job.get("state_reason"))


def exit_status(job: dict[str, Any]) -> str:
    exit_code = job.get("exit_code")
    if isinstance(exit_code, dict):
        status = exit_code.get("status")
        return first_scalar(status)
    return "-"


def stdout_path(job: dict[str, Any]) -> str:
    return first_scalar(
        job.get("stdout_expanded", job.get("standard_output", job.get("stdout")))
    )


def requested_tres(job: dict[str, Any]) -> str:
    if value := job.get("tres_req_str"):
        return first_scalar(value)
    tres = job.get("tres")
    if isinstance(tres, dict):
        return tres_list(tres.get("requested"))
    return "-"


def allocated_tres(job: dict[str, Any]) -> str:
    if value := job.get("tres_alloc_str"):
        return first_scalar(value)
    tres = job.get("tres")
    if isinstance(tres, dict):
        return tres_list(tres.get("allocated"))
    return "-"


def summary_lines(payload: dict[str, Any], source: str) -> list[str]:
    job = response_job(payload)
    if not job:
        return ["No job data returned."]
    if source == "history":
        state = first_scalar((job.get("state") or {}).get("current") if isinstance(job.get("state"), dict) else job.get("state"))
        submit_time = epoch_time((job.get("time") or {}).get("submission") if isinstance(job.get("time"), dict) else None)
        start_time = epoch_time((job.get("time") or {}).get("start") if isinstance(job.get("time"), dict) else None)
        end_time = epoch_time((job.get("time") or {}).get("end") if isinstance(job.get("time"), dict) else None)
        elapsed = first_scalar((job.get("time") or {}).get("elapsed") if isinstance(job.get("time"), dict) else None)
        nodes = first_scalar(job.get("nodes"))
    else:
        state = job_state(job)
        submit_time = epoch_time(job.get("submit_time"))
        start_time = epoch_time(job.get("start_time"))
        end_time = epoch_time(job.get("end_time"))
        elapsed = job_time(job)
        nodes = first_scalar(job.get("nodes"))
    fields = [
        ("job_id", job_id(job)),
        ("name", job_name(job)),
        ("state", state),
        ("reason", state_reason(job)),
        ("partition", job_partition(job)),
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
    return [f"{key}: {value}" for key, value in fields if value != "-"]


@dataclass
class CommandResult:    
    """represents the result of a command execution"""
    ok: bool
    output: str
    error: str = ""


@dataclass
class AppState:
    """represents the state of the application"""
    user: str
    jobs: list[dict[str, Any]] = field(default_factory=list)
    selected: int = 0
    status: str = "Press r to refresh"
    detail_title: str = "Details"
    detail_lines: list[str] = field(default_factory=list)
    last_refresh: float = 0
    next_refresh: float = 0
    running: bool = True
    filter_user: str = ""


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

    def json_command(self, command: str, jid: str) -> tuple[str, list[str]]:
        result = self.run(command, jid, "--json")
        if not result.ok:
            return command, [result.error]
        try:
            payload = json.loads(result.output)
            text = json.dumps(payload, indent=2, sort_keys=True)
        except json.JSONDecodeError:
            text = result.output
        return f"{command} {jid}", text.splitlines()

    def cancel(self, jid: str) -> str:
        result = self.run("cancel", jid)
        if result.ok:
            return result.output.strip() or f"Cancel request sent for job {jid}"
        return result.error

    def submit(
        self,
        script: str,
        name: str,
        partition: str,
        time_limit: str,
        cpus: str,
        tasks: str,
        mem: str,
    ) -> str:
        args = [
            "submit",
            script,
            "--name",
            name,
            "--partition",
            partition,
            "--time",
            time_limit,
            "--cpus",
            cpus,
            "--tasks",
            tasks,
            "--mem",
            mem,
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

    def json_command(self, command: str, jid: str) -> tuple[str, list[str]]:
        return f"{command} {jid}", [f"{command}:{jid}"]

    def cancel(self, jid: str) -> str:
        return f"Cancel request sent for job {jid}"

    def submit(
        self,
        script: str,
        name: str,
        partition: str,
        time_limit: str,
        cpus: str,
        tasks: str,
        mem: str,
    ) -> str:
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
    if y < 0 or y >= height or x >= width:
        return
    win.addstr(y, x, ellipsize(text, width - x), attr)


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


def refresh_jobs(state: AppState, client: MaxwellClient) -> None:
    state.status = "Refreshing jobs..."
    jobs, error = client.jobs(state.filter_user)
    now = time.time()
    state.last_refresh = now
    state.next_refresh = now + REFRESH_SECONDS
    if error:
        state.status = error
        return
    state.jobs = jobs
    if state.selected >= len(state.jobs):
        state.selected = max(0, len(state.jobs) - 1)
    state.status = f"{len(state.jobs)} jobs loaded"
    if state.jobs:
        selected = state.jobs[state.selected]
        state.detail_title = f"Selected {job_id(selected)}"
        state.detail_lines = summarize_job(selected)
    else:
        state.detail_title = "Details"
        state.detail_lines = ["No jobs for current filter."]


def summarize_job(job: dict[str, Any]) -> list[str]:
    fields = [
        ("job_id", job_id(job)),
        ("state", job_state(job)),
        ("partition", job_partition(job)),
        ("name", job_name(job)),
        ("user", job_user(job)),
        ("time", job_time(job)),
        ("nodes", first_scalar(job.get("nodes"))),
        ("submit_time", first_scalar(job.get("submit_time"))),
        ("start_time", first_scalar(job.get("start_time"))),
        ("end_time", first_scalar(job.get("end_time"))),
    ]
    return [f"{key}: {value}" for key, value in fields if value != "-"]


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
        values = [
            job_id(job),
            job_state(job),
            job_partition(job),
            job_name(job),
            job_user(job),
            job_time(job),
        ]
        x = left
        for value, (_, col_width) in zip(values, headers):
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
    while True:
        key = read_key(stdscr)
        if key in ("y", "Y"):
            return True
        if key in ("n", "N", "\x1b", "\n", "\r", curses.KEY_ENTER):
            return False


def prompt_input(stdscr: curses.window, label: str, default: str = "") -> str | None:
    curses.echo()
    try:
        curses.curs_set(1)
    except curses.error:
        pass
    try:
        height, width = stdscr.getmaxyx()
        prompt = f"{label}"
        if default:
            prompt += f" [{default}]"
        prompt += ": "
        addstr(
            stdscr,
            height - 1,
            0,
            " " * max(0, width - 1),
            style("status", curses.A_REVERSE),
        )
        addstr(stdscr, height - 1, 0, prompt, style("status", curses.A_REVERSE))
        stdscr.refresh()
        raw = stdscr.getstr(
            height - 1, min(len(prompt), width - 1), max(1, width - len(prompt) - 1)
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


def submit_flow(stdscr: curses.window, state: AppState, client: MaxwellClient) -> None:
    script = prompt_input(stdscr, "Script path")
    if not script:
        state.status = "Submit aborted"
        return
    default_name = Path(script).name or "maxwell-job"
    name = prompt_input(stdscr, "Name", default_name)
    partition = prompt_input(stdscr, "Partition", "allcpu")
    time_limit = prompt_input(stdscr, "Time seconds", "300")
    cpus = prompt_input(stdscr, "CPUs per task", "1")
    tasks = prompt_input(stdscr, "Tasks", "1")
    mem = prompt_input(stdscr, "Memory MB", "1000")

    # need to do explicit checks to satisfy type narrowing (prompt_input returns Optional[str]).
    if name is None or name == "":
        state.status = "Submit aborted"
        return
    if partition is None or partition == "":
        state.status = "Submit aborted"
        return
    if time_limit is None or time_limit == "":
        state.status = "Submit aborted"
        return
    if cpus is None or cpus == "":
        state.status = "Submit aborted"
        return
    if tasks is None or tasks == "":
        state.status = "Submit aborted"
        return
    if mem is None or mem == "":
        state.status = "Submit aborted"
        return
    summary = f"Submit {name} on {partition}, {tasks} task(s), {cpus} CPU/task, {mem} MB, {time_limit}s?"
    if not prompt_confirm(stdscr, summary):
        state.status = "Submit aborted"
        return
    state.status = client.submit(script, name, partition, time_limit, cpus, tasks, mem)
    refresh_jobs(state, client)


def selected_job_id(state: AppState) -> str | None:
    if not state.jobs:
        return None
    return job_id(state.jobs[state.selected])


def read_key(stdscr: curses.window) -> str | int | None:
    try:
        return stdscr.get_wch()
    except curses.error:
        return None


def handle_key(
    stdscr: curses.window, state: AppState, client: MaxwellClient, key: str | int
) -> None:
    if key in ("q", "Q", "\x1b"):
        state.running = False
    elif key in ("r", "R"):
        refresh_jobs(state, client)
    elif key in (curses.KEY_DOWN, "j", "J") and state.jobs:
        state.selected = min(len(state.jobs) - 1, state.selected + 1)
        state.detail_title = f"Selected {job_id(state.jobs[state.selected])}"
        state.detail_lines = summarize_job(state.jobs[state.selected])
    elif key in (curses.KEY_UP, "k", "K") and state.jobs:
        state.selected = max(0, state.selected - 1)
        state.detail_title = f"Selected {job_id(state.jobs[state.selected])}"
        state.detail_lines = summarize_job(state.jobs[state.selected])
    elif key in (curses.KEY_ENTER, "\n", "\r"):
        if jid := selected_job_id(state):
            state.detail_title, state.detail_lines = client.json_command("job", jid)
            state.status = f"Loaded job {jid}"
    elif key in ("h", "H"):
        if jid := selected_job_id(state):
            state.detail_title, state.detail_lines = client.json_command("history", jid)
            state.status = f"Loaded history {jid}"
    elif key in ("s", "S"):
        submit_flow(stdscr, state, client)
    elif key in ("c", "C"):
        if (jid := selected_job_id(state)) and prompt_confirm(
            stdscr, f"Cancel job {jid}?"
        ):
            state.status = client.cancel(jid)
            refresh_jobs(state, client)
        elif jid:
            state.status = "Cancel aborted"


def self_test_keys() -> None:
    client = SelfTestClient()
    state = AppState(user="alice", filter_user="alice")
    refresh_jobs(state, client)  # type: ignore[arg-type]
    assert len(state.jobs) == 2
    handle_key(None, state, client, "j")  # type: ignore[arg-type]
    assert state.selected == 1
    handle_key(None, state, client, "k")  # type: ignore[arg-type]
    assert state.selected == 0
    handle_key(None, state, client, "\n")  # type: ignore[arg-type]
    assert state.detail_title == "job 1"
    handle_key(None, state, client, "h")  # type: ignore[arg-type]
    assert state.detail_title == "history 1"
    handle_key(None, state, client, "r")  # type: ignore[arg-type]
    assert client.refreshes == 2
    handle_key(None, state, client, "q")  # type: ignore[arg-type]
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
    user = load_user()
    state = AppState(user=user, filter_user=user)
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
