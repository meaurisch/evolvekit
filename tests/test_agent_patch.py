"""The coding agent as the operator (design §5.4): a provider that can `act`
edits the parent's tree from a brief; the tree's diff is the child's block,
refused outside the open files, and every call is billed as the provider
reports it. No test here runs the real `claude` or `copilot`."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from test_driver_tree import _raw
from tree_helpers import SOLVER, change, head, make_repo

from evolvekit import tree
from evolvekit.candidate import Candidate
from evolvekit.config import ConfigError, build_config
from evolvekit.ledger import read_jsonl
from evolvekit.providers.agent_run import agent_env, run_agent, without_session
from evolvekit.providers.base import AgentError, AgentResult, ProviderError, supports_act
from evolvekit.providers.claude_cli import ACT_DISALLOWED, ClaudeCliProvider, parse_act_json, parse_cli_json
from evolvekit.providers.copilot_cli import CopilotCliProvider
from evolvekit.providers.fake import FakeProvider
from evolvekit.search.agent_patch import ACT_MAX_USD, BRIEFS, agent_patch, brief_path, write_brief
from evolvekit.search.driver import Driver
from evolvekit.search.operators import OPERATOR_ROLES, TreeContext

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "harnesses" / "demo-tour"
SOURCE = (ROOT / "examples" / "cli-solver").resolve()

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


class FakeAgentProvider:
    """`act` writes the named files (relative to `cwd`); one dict per call, the last repeated."""

    name = "fake-agent"

    def __init__(self, edits, *, usd=0.12, requests=None, ok=True, error: str | None = None):
        self.edits = edits if isinstance(edits, list) else [edits]
        self.usd, self.requests, self.ok, self.error = usd, requests, ok, error
        self.calls: list[dict] = []

    def complete(self, *a, **k):
        raise AssertionError("agent_patch acts, it does not complete")

    def act(self, brief, *, cwd, model, timeout_s, **options):
        self.calls.append({"prompt": brief, "cwd": Path(cwd), "model": model, "timeout_s": timeout_s, **options})
        if self.error:
            raise self.error if isinstance(self.error, Exception) else ProviderError(self.error)
        edits = self.edits[min(len(self.calls), len(self.edits)) - 1]
        for rel, text in edits.items():
            (Path(cwd) / rel).write_text(text, encoding="utf-8", newline="\n")
        return AgentResult(ok=self.ok, text="done" if self.ok else "stopped at the turn limit",
                           usd=self.usd, requests=self.requests, input_tokens=1000, output_tokens=200)


@pytest.fixture
def project(tmp_path):
    repo = make_repo(tmp_path)
    yield repo, head(repo)
    tree.remove_all(tmp_path / "run")  # Windows keeps a temporary folder otherwise


@pytest.fixture
def config(project, tmp_path):
    repo, base = project
    raw = _raw(repo, base)
    raw["problem"]["description"] = "Make VALUE small.\nWhat to improve: Smaller VALUE, same output."
    return build_config(raw, base_dir=tmp_path)


@pytest.fixture
def ctx(project, tmp_path):
    repo, base = project
    return TreeContext(tmp_path / "run", tree.Repo(repo / "app dir"), base, ("solver.py",), "g001-c0001.draft")


@pytest.fixture
def parent(project):
    repo, _ = project
    block = change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "VALUE = 3")})
    return Candidate(id="g000-c0000", generation=0, block=block, source="", operator="human-seed",
                     score=-3.0, kpis={"value": 3.0, "value_raw": 3.0})


def _act(agent, *, config, parent, ctx, best=None, worst=None, failures=(), **extra):
    return agent_patch(config=config, provider=agent, model_role="strong", parent=parent, parent_result=None,
                       context=ctx, best=best, worst=worst, failures=failures, **extra)


def _brief_path(ctx) -> Path:
    path = ctx.run_dir / "work" / BRIEFS / ctx.draft_id / "brief.md"
    assert path == brief_path(ctx.run_dir, ctx.draft_id)
    return path


def test_agent_patch_turns_edits_into_a_diff(ctx, config, parent):
    agent = FakeAgentProvider({"solver.py": SOLVER.replace("VALUE = 5", "VALUE = 1")})
    result = _act(agent, config=config, parent=parent, ctx=ctx, best=parent, failures=["Traceback: boom"])
    assert result.ok and result.mode == "agent" and "+VALUE = 1" in result.block and "-VALUE = 5" in result.block
    assert "a/app dir/solver.py" in result.block, "cumulative, against the base, toplevel paths"
    assert agent.calls[0]["cwd"] == tree.tree_dir(ctx.run_dir, ctx.draft_id) / "app dir", "it acts in the parent's tree"
    assert agent.calls[0]["model"] == "strong" and agent.calls[0]["timeout_s"] == 1800.0
    assert not tree.tree_dir(ctx.run_dir, ctx.draft_id).exists(), "the draft is removed"
    assert tree.applies(ctx.repo, ctx.base, result.block)


def test_the_agent_gets_a_short_prompt_naming_the_brief_file(ctx, config, parent):
    agent = FakeAgentProvider({"solver.py": "VALUE = 1\n"})
    _act(agent, config=config, parent=parent, ctx=ctx)
    path = _brief_path(ctx)
    prompt = agent.calls[0]["prompt"]
    assert str(path) in prompt and "Read it first" in prompt and len(prompt) < 600, \
        "the brief is a file (decision 39), never on a command line"
    assert path.is_file() and not str(path).startswith(str(tree.tree_dir(ctx.run_dir, ctx.draft_id))), \
        "outside every tree, so never in the diff"
    assert agent.calls[0]["read_dirs"] == (path.parent,) and path.parent.name == ctx.draft_id, \
        "the agent may read this call's brief folder, and no other call's"


def test_agent_diff_outside_open_is_refused(ctx, config, parent):
    agent = FakeAgentProvider({"bench.py": "print('always fast')\n", "solver.py": "VALUE = 1\n"})
    result = _act(agent, config=config, parent=parent, ctx=ctx)
    assert not result.ok and not result.provider_error
    assert "changes files outside the open files: app dir/bench.py (only solver.py may change)" in result.error
    assert result.completion is not None, "the call was made: it is still paid for"
    brief = _brief_path(ctx).read_text(encoding="utf-8")
    assert "Do not run the benchmark yourself" in brief and "- solver.py" in brief and "Change only the files listed" in brief
    assert not tree.tree_dir(ctx.run_dir, ctx.draft_id).exists()


def test_the_brief_says_what_design_5_4_lists(ctx, config, parent):
    best = Candidate(id="g002-c0003", generation=2, block="", source="", operator="agent", score=-1.0,
                     kpis={"value": 1.0, "value_raw": 9.0, "_internal": 1.0, "time_s": 0.25})
    worst = Candidate(id="g001-c0001", generation=1, block="", source="", operator="diff", score=-8.0,
                      kpis={"value": 8.0})
    failures = ["x" * 3000 + "THE END", "stage build: exit 1\nbad \udcff byte"]
    _act(FakeAgentProvider({"solver.py": "VALUE = 1\n"}), config=config, parent=parent, ctx=ctx,
         best=best, worst=worst, failures=failures)
    brief = _brief_path(ctx).read_text(encoding="utf-8")
    assert brief.startswith("# What to change\n\nMake VALUE small.\n")
    assert "What to improve:" not in brief, "the engineer's line has its own section"
    assert "## What the engineer wants\nSmaller VALUE, same output." in brief
    assert "## The files you may change\n- solver.py\n" in brief
    assert "--- a/solver.py" in brief and "+VALUE = 3" in brief, "the parent's diff, relative to this folder"
    assert "- The best so far: g002-c0003 (value 1, time_s 0.25)" in brief
    assert "- The worst so far: g001-c0001 (value 8)" in brief
    assert "value_raw" not in brief and "_internal" not in brief
    assert "x" * 1993 + "THE END" in brief and "x" * 1994 + "THE END" not in brief, "each failure's last 2000 characters"
    assert "bad \ufffd byte" in brief, "a lone surrogate reaches the agent as U+FFFD"
    assert "Do not commit" in brief and "Keep the program's tests passing" in brief


def test_write_brief_without_history(tmp_path):
    text = write_brief(tmp_path / "b.md", description="Make it fast.", brief="", open_files=["a.py", "lib/*.py"],
                       parent_diff="", best=None, worst=None, failures=[])
    assert "Improve the measure the study is about." in text
    assert "(none: this is the starting point)" in text and "## The last failures\n```\n(none)\n```" in text
    assert "best so far" not in text and "worst so far" not in text
    assert (tmp_path / "b.md").read_text(encoding="utf-8") == text


def test_agent_patch_counts_cost(ctx, config, parent):
    result = _act(FakeAgentProvider({"solver.py": "VALUE = 1\n"}), config=config, parent=parent, ctx=ctx)
    c = result.completion
    assert (c.usd, c.requests, c.input_tokens, c.output_tokens, c.provider, c.model) == \
        (0.12, None, 1000, 200, "fake-agent", "strong")
    copilot = _act(FakeAgentProvider({"solver.py": "VALUE = 0\n"}, usd=None, requests=1.0),
                   config=config, parent=parent, ctx=ctx)
    assert copilot.completion.requests == 1.0 and copilot.completion.usd is None


def test_an_agent_stopped_at_its_limit_keeps_its_edits(ctx, config, parent):
    result = _act(FakeAgentProvider({"solver.py": "VALUE = 2\n"}, ok=False), config=config, parent=parent, ctx=ctx)
    assert result.ok and "+VALUE = 2" in result.block, "decision D14: the edits stand"


def test_nothing_changed_and_provider_errors(ctx, config, parent):
    same = _act(FakeAgentProvider({}), config=config, parent=parent, ctx=ctx)
    assert not same.ok and same.error == "the agent changed nothing" and not same.provider_error
    broken = _act(FakeAgentProvider({}, error="claude-cli provider: exit 1: boom"), config=config, parent=parent,
                  ctx=ctx)
    assert not broken.ok and broken.provider_error and "boom" in broken.error and broken.completion is None
    cannot = _act(FakeProvider(responses=["x"]), config=config, parent=parent, ctx=ctx)
    assert not cannot.ok and cannot.provider_error and "cannot act" in cannot.error
    assert not tree.tree_dir(ctx.run_dir, ctx.draft_id).exists()


def test_time_and_money_bounds_reach_the_agent(ctx, config, parent):
    agent = FakeAgentProvider({"solver.py": "VALUE = 1\n"})
    stop = threading.Event()
    _act(agent, config=config, parent=parent, ctx=ctx, time_left_s=120.0, left_usd=0.3, cancel=stop)
    _act(agent, config=config, parent=parent, ctx=ctx)
    first, second = agent.calls
    assert first["timeout_s"] == 120.0, "the smaller of act_timeout_s and the study's time left (decision 36)"
    assert first["max_usd"] == 0.3 and first["cancel"] is stop, "never more than what is left of the budget"
    assert second["max_usd"] == ACT_MAX_USD == 0.50, "act_max_usd, $0.50 when not set"
    raw = _raw(*_project_of(config))
    raw["models"]["strong"]["options"] = {"act_max_usd": 2.0}
    _act(agent, config=build_config(raw, base_dir=config.base_dir), parent=parent, ctx=ctx, left_usd=1.25)
    assert agent.calls[2]["max_usd"] == 1.25


def _project_of(config):
    tree_cfg = config.problem.tree
    return Path(tree_cfg.repo).parent, tree_cfg.base


# -- what every call is billed -------------------------------------------------


def test_a_failed_call_is_billed_as_reported_or_at_its_cap(ctx, config, parent):
    lost = _act(FakeAgentProvider({}, error=AgentError("timed out", unreported=True)), config=config, parent=parent,
                ctx=ctx, left_usd=0.4)
    assert not lost.ok and lost.provider_error and "timed out" in lost.error
    assert lost.completion.usd == 0.4 and lost.completion.raw["priced_from"] == "cap", "nothing came back: the cap"
    told = _act(FakeAgentProvider({}, error=AgentError("API down", usd=0.07, input_tokens=9)), config=config,
                parent=parent, ctx=ctx)
    assert told.provider_error and told.completion.usd == 0.07 and told.completion.input_tokens == 9
    assert "priced_from" not in told.completion.raw
    copilot = _act(FakeAgentProvider({}, error=AgentError("timed out", usd=0.0, requests=1.0)), config=config,
                   parent=parent, ctx=ctx)
    assert copilot.completion.requests == 1.0 and copilot.completion.usd == 0.0
    never = _act(FakeAgentProvider({}, error=ProviderError("could not run claude")), config=config, parent=parent,
                 ctx=ctx)
    assert never.provider_error and never.completion is None, "it never started: nothing was spent"
    silent = _act(FakeAgentProvider({"solver.py": "VALUE = 1\n"}, usd=None, requests=None), config=config,
                  parent=parent, ctx=ctx)
    assert silent.ok and silent.completion.usd == ACT_MAX_USD and silent.completion.raw["priced_from"] == "cap"


def test_the_token_cap_counts_uncached_input_and_output(ctx, config, parent):
    payload = {"result": "ok", "total_cost_usd": 0.4, "usage": {
        "input_tokens": 120, "cache_read_input_tokens": 900_000, "cache_creation_input_tokens": 50_000,
        "output_tokens": 30}}
    result = parse_act_json(json.dumps(payload))
    assert (result.input_tokens, result.cached_input_tokens, result.output_tokens) == (120, 950_000, 30)
    assert parse_cli_json(json.dumps(payload), fallback_model="m").input_tokens == 950_120, \
        "a completion still counts its cache tokens"

    class Cached(FakeAgentProvider):
        def act(self, brief, **options):
            super().act(brief, **options)
            return result

    done = _act(Cached({"solver.py": "VALUE = 1\n"}), config=config, parent=parent, ctx=ctx)
    assert done.completion.total_tokens == 150 and done.completion.raw["cached_input_tokens"] == 950_000


def test_failed_calls_reach_the_ledger_and_the_log(project, tmp_path):
    repo, base = project
    raw = _raw(repo, base, generations=1)
    raw["search"]["operators"] = {"agent_patch": 1.0}
    lines: list[str] = []
    agent = FakeAgentProvider({}, error=AgentError("claude-cli provider: the agent timed out after 9s",
                                                   unreported=True))
    Driver(build_config(raw, base_dir=tmp_path), run_dir=tmp_path / "run", providers={"strong": agent},
           log=lines.append).run()
    usage = list(read_jsonl(tmp_path / "run" / "usage.jsonl"))
    assert usage and all(u["priced_from"] == "cap" and u["usd"] == 0.5 for u in usage)
    assert any("the agent's cost was not reported; charged its cap $0.50" in line for line in lines)
    rows = [r for r in read_jsonl(tmp_path / "run" / "runs.jsonl") if r["generation"] == 1]
    assert rows and all(r["rejected"] and "timed out" in r["reject_reason"] for r in rows)


# -- files the agent adds outside the open files --------------------------------


def test_a_stray_file_is_dropped_and_the_change_kept(ctx, config, parent):
    agent = FakeAgentProvider({"scratch.txt": "notes\n", "solver.py": "VALUE = 1\n"})
    result = _act(agent, config=config, parent=parent, ctx=ctx)
    assert result.ok and "+VALUE = 1" in result.block and "scratch.txt" not in result.block
    assert result.meta["dropped"] == ["app dir/scratch.txt"]
    only = _act(FakeAgentProvider({"notes [1].md": "x\n"}), config=config, parent=parent, ctx=ctx)
    assert not only.ok and only.error.startswith("the agent changed nothing (the files it added outside")
    assert "app dir/notes [1].md" in only.error, "a glob character in a name is a literal path"


def test_a_tracked_file_outside_open_still_refuses(ctx, config, parent):
    class Renames(FakeAgentProvider):
        def act(self, brief, *, cwd, **options):
            os.replace(Path(cwd) / "bench.py", Path(cwd) / "bench2.py")
            return super().act(brief, cwd=cwd, **options)

    renamed = _act(Renames({"solver.py": "VALUE = 1\n"}), config=config, parent=parent, ctx=ctx)
    assert not renamed.ok and "outside the open files: app dir/bench.py" in renamed.error, \
        "a rename is a deletion of a tracked file (--no-renames)"
    edited = _act(FakeAgentProvider({"bench.py": "print('fast')\n"}), config=config, parent=parent, ctx=ctx)
    assert not edited.ok and "outside the open files: app dir/bench.py" in edited.error


# -- the providers ---------------------------------------------------------

HELP = """Options:
  --add-dir <directories...>   Additional directories
  --max-budget-usd <amount>    Maximum dollar amount
  --max-turns <turns>          Maximum agentic turns
  --permission-prompts <target>
  --setting-sources <sources>
"""


def _claude(seen, *, help_text=HELP, stdout=None, returncode=0, raises=None):
    payload = {"result": "ok", "total_cost_usd": 0.31, "usage": {"input_tokens": 10, "output_tokens": 5}}

    def runner(argv, **kwargs):
        if argv[-1] == "--help":
            seen.setdefault("helps", 0)
            seen["helps"] += 1
            return subprocess.CompletedProcess(argv, 0, help_text, "")
        seen.update(kwargs, argv=argv)
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(argv, returncode, json.dumps(payload) if stdout is None else stdout, "")

    return ClaudeCliProvider(runner=runner, executable=sys.executable)


def test_claude_act_argv(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setenv("CLAUDE_CODE_GIT_BASH_PATH", "C:/git/bash.exe")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "secret")
    seen: dict = {}
    provider = _claude(seen)
    assert provider.build_act_argv(model="m")[1:] == [
        "-p", "--output-format", "json", "--max-turns", "30", "--no-session-persistence", "--strict-mcp-config",
        "--disable-slash-commands", "--setting-sources", "project", "--permission-prompts", "none",
        "--model", "m", "--allowedTools", "Edit Write Read Glob Grep Bash",
        "--disallowedTools", "WebFetch", "WebSearch", "Task", "Agent",
        "Bash(git push:*)", "Bash(git commit:*)", "Bash(git stash:*)", "Bash(git config:*)", "Bash(git checkout:*)",
        "Bash(git switch:*)", "Bash(git reset:*)", "Bash(git branch:*)", "Bash(git tag:*)", "Bash(git worktree:*)",
        "Bash(git remote:*)"]
    assert list(ACT_DISALLOWED[4:]) == provider.build_act_argv(model="m")[-11:], \
        "each rule its own argv item: a rule holds a space"
    result = provider.act("read the brief", cwd=tmp_path, model="m", timeout_s=900, read_dirs=(tmp_path / "b",),
                          max_usd=0.75)
    assert seen["cwd"] == str(tmp_path) and seen["input"] == "read the brief" and seen["timeout"] == 900
    assert seen["argv"][-4:] == ["--add-dir", str(tmp_path / "b"), "--max-budget-usd", "0.7500"]
    env = seen["env"]
    assert "CLAUDECODE" not in env and "CLAUDE_CODE_ENTRYPOINT" not in env, "never the launching session's child"
    assert env["CLAUDE_CODE_GIT_BASH_PATH"] == "C:/git/bash.exe" and env["ANTHROPIC_API_KEY"] == "k"
    assert "AZURE_OPENAI_API_KEY" not in env, "evolvekit's own keys are not the agent's business"
    assert result == AgentResult(ok=True, text="ok", usd=0.31, requests=None, input_tokens=10, output_tokens=5)
    assert supports_act(provider) and not supports_act(FakeProvider(responses=["x"]))
    provider.act("again", cwd=tmp_path, model="", timeout_s=900)
    assert seen["helps"] == 1, "the flags are read from --help once per executable"
    assert "--model" not in seen["argv"] and "--max-budget-usd" not in seen["argv"]


def test_claude_act_passes_only_the_flags_its_help_lists(tmp_path):
    seen: dict = {}
    provider = _claude(seen, help_text="Options:\n  --add-dir <directories...>\n")
    argv = provider.build_act_argv(model="m", max_usd=1.0)
    assert "--max-turns" not in argv and "--max-budget-usd" not in argv and "--permission-prompts" not in argv, \
        "bounded by time and money only when the CLI has no --max-turns (claude 2.1.304 lists none)"
    assert argv[1:4] == ["-p", "--output-format", "json"] and "--allowedTools" in argv


def test_claude_act_failures(tmp_path):
    limit = json.dumps({"is_error": True, "subtype": "error_max_turns", "total_cost_usd": 0.5,
                        "usage": {"input_tokens": 7, "output_tokens": 3}})
    result = _claude({}, stdout=limit, returncode=1).act("b", cwd=tmp_path, model="m", timeout_s=9)
    assert not result.ok and result.usd == 0.5 and "turn limit" in result.text, "its edits are used as they are"
    budget = json.dumps({"is_error": True, "subtype": "error_max_budget_usd", "total_cost_usd": 1.0})
    assert not _claude({}, stdout=budget).act("b", cwd=tmp_path, model="m", timeout_s=9).ok
    other = json.dumps({"is_error": True, "subtype": "error_during_execution", "result": "API down",
                        "total_cost_usd": 0.21, "usage": {"input_tokens": 4, "output_tokens": 2}})
    with pytest.raises(AgentError, match="API down") as told:
        _claude({}, stdout=other, returncode=1).act("b", cwd=tmp_path, model="m", timeout_s=9)
    assert (told.value.usd, told.value.input_tokens, told.value.unreported) == (0.21, 4, False), \
        "an error the agent reported bills what it reported"
    for stdout, returncode, message in (("not json", 2, "exit 2"), ("", 0, "not JSON")):
        with pytest.raises(AgentError, match=message) as crash:
            _claude({}, stdout=stdout, returncode=returncode).act("b", cwd=tmp_path, model="m", timeout_s=9)
        assert crash.value.unreported and crash.value.usd is None, "no cost came back: the operator charges the cap"
    with pytest.raises(AgentError, match="timed out after 9s") as slow:
        _claude({}, raises=subprocess.TimeoutExpired(["claude"], 9)).act("b", cwd=tmp_path, model="m", timeout_s=9)
    assert slow.value.unreported
    with pytest.raises(AgentError, match="stopped") as stopped:
        _claude({}, raises=AgentError("stopped", unreported=True)).act("b", cwd=tmp_path, model="m", timeout_s=9)
    assert stopped.value.unreported
    with pytest.raises(ProviderError, match="could not run") as never:
        _claude({}, raises=OSError("no such file")).act("b", cwd=tmp_path, model="m", timeout_s=9)
    assert not isinstance(never.value, AgentError), "it never started: nothing to bill"


def _copilot(seen):
    def runner(argv, **kwargs):
        seen.update(kwargs, argv=argv)
        return subprocess.CompletedProcess(argv, 0, "I changed solver.py.\n", "")

    provider = CopilotCliProvider(runner=runner, executable="copilot", windows=False)
    provider._resolve = lambda: (["copilot"], False)  # type: ignore[method-assign]
    return provider


def test_copilot_act_argv(tmp_path):
    """Unverified against a real Copilot CLI (none on this machine): the argv is GitHub's documented flags."""
    seen: dict = {}
    provider = _copilot(seen)
    argv = provider.build_act_argv("read the brief", model="m", read_dirs=(tmp_path / "b",))
    assert argv[-7:] == ["-p", "read the brief", "--allow-tool=write", "--allow-tool=shell", "--no-ask-user", "-s",
                         "--model=m"]
    assert argv[1:3] == ["--add-dir", str(tmp_path / "b")]
    assert "--deny-tool=shell" not in argv and "--deny-tool=write" not in argv
    result = provider.act("read the brief", cwd=tmp_path, model="m", timeout_s=900)
    assert result.ok and result.requests == 1.0 and result.usd == 0.0 and result.text == "I changed solver.py."
    assert seen["cwd"] == str(tmp_path) and seen["input"] is None and seen["timeout"] == 900
    assert seen["env"]["COPILOT_AUTO_UPDATE"] == "false"
    assert supports_act(provider)


def test_copilot_act_failures_charge_a_request(tmp_path):
    from evolvekit.providers.copilot_cli import MISSING

    for raised in (subprocess.TimeoutExpired(["copilot"], 9), AgentError("stopped", unreported=True)):
        def runner(argv, _raised=raised, **kwargs):
            raise _raised

        provider = CopilotCliProvider(runner=runner, executable="copilot", windows=False)
        provider._resolve = lambda: (["copilot"], False)  # type: ignore[method-assign]
        with pytest.raises(AgentError) as charged:
            provider.act("b", cwd=tmp_path, model="m", timeout_s=9)
        assert (charged.value.requests, charged.value.usd, charged.value.unreported) == (1.0, 0.0, False)

    def missing(argv, **kwargs):
        raise OSError("[WinError 2] The system cannot find the file specified")

    provider = CopilotCliProvider(runner=missing, executable="copilot", windows=False)
    provider._resolve = lambda: (["copilot"], False)  # type: ignore[method-assign]
    with pytest.raises(ProviderError, match="not installed") as gone:
        provider.act("b", cwd=tmp_path, model="m", timeout_s=9)
    assert str(gone.value) == MISSING and gone.value.kind == "missing"


def test_copilot_act_refuses_a_shim_and_a_long_prompt(tmp_path):
    provider = CopilotCliProvider(runner=lambda *a, **k: None, executable="copilot", windows=True)
    provider._resolve = lambda: (["copilot.cmd"], True)  # type: ignore[method-assign]
    with pytest.raises(ProviderError, match="could not be run safely"):
        provider.build_act_argv("b", model="m")
    provider._resolve = lambda: (["copilot.exe"], False)  # type: ignore[method-assign]
    with pytest.raises(ProviderError, match="too long"):
        provider.build_act_argv("b" * 40_000, model="m")


# -- the shared environment and the bounded run -------------------------------


def test_without_session_is_the_gates_rule():
    source = {"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "x", "CLAUDE_AGENT_SDK_VERSION": "1",
              "CLAUDE_CODE_USE_BEDROCK": "1", "ANTHROPIC_MODEL": "m", "CLAUDE_CLI_PATH": "c", "PATH": "p"}
    assert without_session(source) == {"CLAUDE_CODE_USE_BEDROCK": "1", "ANTHROPIC_MODEL": "m",
                                       "CLAUDE_CLI_PATH": "c", "PATH": "p"}
    kept = agent_env({**source, "GH_TOKEN": "t", "OPENROUTER_API_KEY": "s"}, keep=("GH_TOKEN",))
    assert kept["GH_TOKEN"] == "t" and "OPENROUTER_API_KEY" not in kept and kept["PYTHONDONTWRITEBYTECODE"] == "1"


AGENT = """import sys, time, pathlib
prompt = sys.stdin.read()
pathlib.Path("seen.txt").write_text(prompt, encoding="utf-8")
print('{"result": "edited"}')
time.sleep(float(sys.argv[1]))
"""


def test_run_agent_feeds_stdin_in_cwd_and_a_stop_kills_it(tmp_path):
    script = tmp_path / "agent.py"
    script.write_text(AGENT, encoding="utf-8")
    work = tmp_path / "work dir"
    work.mkdir()
    done = run_agent([sys.executable, str(script), "0"], input="the prompt", cwd=str(work), timeout=60,
                     env=dict(os.environ))
    assert done.returncode == 0 and '"edited"' in done.stdout and (work / "seen.txt").read_text() == "the prompt"
    stop = threading.Event()
    threading.Timer(0.5, stop.set).start()
    began = time.perf_counter()
    with pytest.raises(ProviderError, match="stopped"):
        run_agent([sys.executable, str(script), "60"], input="", cwd=str(work), timeout=60, env=dict(os.environ),
                  cancel=stop)
    assert time.perf_counter() - began < 30, "a Stop request ends the call by killing its tree"
    with pytest.raises(subprocess.TimeoutExpired):
        run_agent([sys.executable, str(script), "60"], input="", cwd=str(work), timeout=1, env=dict(os.environ))


# -- the operator in the search ------------------------------------------------


def test_the_driver_dispatches_agent_patch_and_bills_it(project, tmp_path):
    repo, base = project
    raw = _raw(repo, base, generations=1)
    raw["search"]["operators"] = {"agent_patch": 1.0}
    raw["budget"] = {"max_usd": 0.6}
    agent = FakeAgentProvider([{"solver.py": SOLVER.replace("VALUE = 5", f"VALUE = {v}")} for v in (3, 2)])
    summary = Driver(build_config(raw, base_dir=tmp_path), run_dir=tmp_path / "run",
                     providers={"strong": agent}).run()
    assert OPERATOR_ROLES["agent_patch"] == "strong" and len(agent.calls) == 2
    rows = [r for r in read_jsonl(tmp_path / "run" / "runs.jsonl") if r["generation"] == 1]
    assert [r["operator"] for r in rows] == ["agent", "agent"] and not any(r["rejected"] for r in rows)
    assert summary.best.kpis["value"] == 2.0
    usage = list(read_jsonl(tmp_path / "run" / "usage.jsonl"))
    assert [u["usd"] for u in usage] == [0.12, 0.12] and all(u["priced_from"] == "provider" for u in usage)
    first, second = agent.calls
    assert first["cancel"] is not None and first["timeout_s"] == 1800.0
    assert first["max_usd"] == pytest.approx(0.5) and second["max_usd"] == pytest.approx(0.48), \
        "act_max_usd ($0.50), never more than what is left of budget.max_usd"
    assert Path(first["read_dirs"][0]).parent.name == BRIEFS
    briefs = sorted((tmp_path / "run" / "work" / BRIEFS).glob("*/brief.md"))
    assert [p.parent.name for p in briefs] == [f"{r['id']}.draft" for r in rows], "one brief per call, kept"
    seed = next(read_jsonl(tmp_path / "run" / "runs.jsonl"))["id"]
    assert f"- The best so far: {seed} (value 5)" in briefs[0].read_text(encoding="utf-8")


def test_operator_mix_includes_agent_patch_for_trees():
    from evolvekit.harness.compile import ACT_PROVIDERS, OPERATORS_CODE_AGENT, compile_study
    from evolvekit.harness.manifest import load_harness
    from evolvekit.harness.plan import Plan
    from evolvekit.harness.study import study_from_template

    assert ACT_PROVIDERS == ("claude-cli", "copilot-cli")
    harness = load_harness(DEMO)
    study = study_from_template(harness, "faster", "F")
    study.code.repo, study.training, study.budget.ai_enabled = str(SOURCE), ["cases/a.json"], True
    study.application_path = str(SOURCE / "solver.py")
    for provider, has in (("claude-cli", True), ("copilot-cli", True), ("azure", False)):
        models = {r: {"provider": provider, "model": ""} for r in ("small", "strong")}
        ops = compile_study(study, harness, Plan.fixed(), models=models)[0]["search"]["operators"]
        assert ("agent_patch" in ops) is has and ops.get("diff")
    assert OPERATORS_CODE_AGENT == {"agent_patch": 0.5, "diff": 0.5}
    settings = study_from_template(harness, "tune", "T")
    settings.budget.ai_enabled = True
    models = {r: {"provider": "claude-cli", "model": ""} for r in ("small", "strong")}
    assert "agent_patch" not in compile_study(settings, harness, Plan.fixed(), models=models)[0]["search"]["operators"]


def test_agent_patch_needs_a_tree(minimal_raw, tmp_path):
    with pytest.raises(ConfigError, match="agent_patch changes a source tree"):
        build_config({**minimal_raw, "search": {"operators": {"agent_patch": 1.0}}}, base_dir=tmp_path)
