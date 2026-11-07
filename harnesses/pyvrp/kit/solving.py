"""PyVRP's search in a process of its own, stopped from outside when it does
not come back.

For some settings PyVRP 0.14's local search goes round in circles, and then
`pyvrp.solve` never returns: its stopping criterion is only asked between
iterations. So the search runs in a child process (child.py) that reports
every new best plan the moment it has one. A search still running `grace`
after the time limit is stopped, and the best plan it reported is the result;
the summary says so (`stopped`). Until its first plan a search gets
`patience` instead: a first plan for a large request can take a while, and
without one there is nothing to hand back but an empty plan.

evolvekit stops a runner together with everything it started, so the child
never outlives a run that is stopped or timed out.
"""

from __future__ import annotations

import collections
import json
import os
import pickle
import subprocess
import sys
import tempfile
import threading
import time

import evk_harness as evk

from . import plan

CHILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "child.py")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def grace(limit):
    """How long past the time limit a search that has a plan may take to come back."""
    return max(2.0, 0.1 * limit)


def patience(limit):
    """How long past the time limit a search may take to come back with its first plan."""
    return max(grace(limit), 0.25 * limit + 15.0)


class _Reports(threading.Thread):
    """What the child reports on stdout: its latest best plan and iteration count."""

    def __init__(self, stream):
        super().__init__(daemon=True)
        self.stream = stream
        self.lock = threading.Lock()
        self.best = None
        self.iterations = 0
        self.done = False
        self.over = threading.Event()
        """Set when the child says it is done, or its stdout closes."""

    def run(self):
        try:
            for line in self.stream:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue  # the half-written line of a stopped child
                with self.lock:
                    if "best" in message:
                        self.best = message["best"]
                    if "iterations" in message:
                        self.iterations = int(message["iterations"])
                    if message.get("done"):
                        self.done = True
                        self.over.set()
        finally:
            self.over.set()

    def has_plan(self):
        with self.lock:
            return self.best is not None


class _Errors(threading.Thread):
    """The child's stderr, passed on to the runner's and its last lines kept."""

    def __init__(self, stream):
        super().__init__(daemon=True)
        self.stream = stream
        self.lines = collections.deque(maxlen=20)

    def run(self):
        for line in self.stream:
            self.lines.append(line.rstrip())
            sys.stderr.write(line)
        sys.stderr.flush()

    def last(self):
        said = [line for line in self.lines if line.strip()]
        return said[-1] if said else ""


def _kill(child):
    """Stop the child and whatever it started. On Windows a virtual
    environment's python.exe is a launcher that runs the real interpreter as
    its own child, so the whole tree goes."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(child.pid)], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    if child.poll() is None:
        child.kill()
    child.wait()


def rebuild(data, routes):
    """The PyVRP solution of `routes`, as child.routes_of reports them."""
    from pyvrp import Activity, ActivityType, Route, Solution

    return Solution(data, [
        Route(data, [Activity(ActivityType(kind), index) for kind, index in visits], vehicle_type)
        for vehicle_type, visits in routes
    ])


def search(built, values, scale, limit, seed):
    """Solve `built` (request.Built) with the settings `values` (params.resolve)
    for `limit` seconds; a plan.Solved."""
    started = time.perf_counter()
    deadline = time.time() + limit
    stopped = False
    with tempfile.TemporaryDirectory(prefix="evk-pyvrp-") as folder:
        problem = os.path.join(folder, "problem.pickle")
        with open(problem, "wb") as handle:
            pickle.dump(built.data, handle, protocol=pickle.HIGHEST_PROTOCOL)
        settings = os.path.join(folder, "settings.json")
        with open(settings, "w", encoding="utf-8") as handle:
            json.dump({"values": values, "scale": scale}, handle)
        child = subprocess.Popen(
            [sys.executable, CHILD, problem, settings, str(seed), repr(deadline)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
        )
        reports, errors = _Reports(child.stdout), _Errors(child.stderr)
        reports.start()
        errors.start()
        try:
            while not reports.over.is_set():
                left = deadline + (grace(limit) if reports.has_plan() else patience(limit)) - time.time()
                if left <= 0:
                    stopped = True
                    break
                reports.over.wait(min(left, 0.25))
            if reports.done:
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if child.poll() is None:
                _kill(child)
            reports.join(timeout=5)
            errors.join(timeout=5)
            child.stdout.close()
            child.stderr.close()
    runtime_s = time.perf_counter() - started
    if not stopped and not reports.done:
        raise evk.HarnessStop("PyVRP stopped with an error: {}".format(
            errors.last() or "exit code {}".format(child.returncode)))
    if reports.best is None:
        best = rebuild(built.data, [])
    else:
        best = rebuild(built.data, reports.best)
    if stopped:
        sys.stderr.write(
            "PyVRP had not come back {:.1f} s after the time limit of {:g} s and was stopped; {}.\n".format(
                time.time() - deadline, limit,
                "the plan is the best it had found" if reports.best is not None else "it had found no plan yet"))
    return plan.Solved(best, built, iterations=reports.iterations, runtime_s=runtime_s, stopped=stopped)
