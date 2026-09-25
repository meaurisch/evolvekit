"""The dashboard: a read-only server and one self-contained page.

What is pinned down here is the contract, not the pixels: the page and
`status --json` get the *same* document; the server can read the run directory
and nothing else; the page needs no network; the exported file is safe to open
and safe to build from a run that recorded hostile text.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from evolvekit.cli import main
from evolvekit.dashboard import PAGE, DashboardServer, export_html
from evolvekit.status import build_status

HOSTILE = 'x = "</script><script>alert(1)</script>"\n'


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    """A small finished run, written by hand: a seed, a better child with a
    block that tries to break out of a script element, and one failure."""
    directory = tmp_path / "run"
    (directory / "work" / "stage_out").mkdir(parents=True)
    rows = [
        {"id": "g000-c0001", "generation": 0, "operator": "human-seed", "block": "x = 1\n",
         "score": -100.0, "kpis": {"cost": 100.0}, "competes": True, "stages_reached": ["static", "full"]},
        {"id": "g001-c0002", "generation": 1, "operator": "rewrite", "parent_id": "g000-c0001",
         "block": HOSTILE, "score": -90.0, "kpis": {"cost": 90.0}, "competes": True,
         "stages_reached": ["static", "full"]},
    ]
    (directory / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    log = "work/stage_out/g001-c0003.full.stderr.log"
    (directory / log).write_text("Segmentation fault\n", encoding="utf-8")
    events = [
        {"seq": 1, "ts": "2026-09-19T10:00:00.000+00:00", "session": "s", "pid": 1, "type": "run_started",
         "objective": "cost", "direction": "minimize", "first_generation": 1, "generations_planned": 1,
         "stages": [{"id": "full", "kind": "command", "final": True, "timeout_s": 60, "seeds": 1}]},
        {"seq": 2, "ts": "2026-09-19T10:00:05.000+00:00", "session": "s", "pid": 1, "type": "eval_finished",
         "candidate_id": "g001-c0003", "stage": "full", "seed": 0, "private": False, "ok": False,
         "failure": "exit code 139", "stderr_tail": "Segmentation fault", "stderr_log": log,
         "argv": ["./solver", "--seed", "0"], "duration_s": 1.5},
        {"seq": 3, "ts": "2026-09-19T10:00:09.000+00:00", "session": "s", "pid": 1, "type": "run_finished",
         "stop_reason": "generations exhausted"},
    ]
    (directory / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    (tmp_path / "secret.log").write_text("not part of the run", encoding="utf-8")
    return directory


@pytest.fixture
def server(run_dir: Path):
    dashboard = DashboardServer(run_dir, port=0)
    dashboard.start()
    yield dashboard
    dashboard.stop()


def _get(server: DashboardServer, path: str) -> tuple[int, str, str]:
    try:
        with urllib.request.urlopen(server.url + path, timeout=10) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type", ""), error.read().decode("utf-8")


# -- one source of truth ---------------------------------------------------


def test_the_page_and_status_json_are_given_the_same_document(server, run_dir):
    status, content_type, body = _get(server, "api/status")
    assert status == 200 and content_type.startswith("application/json")
    served, direct = json.loads(body), build_status(run_dir)
    for document in (served, direct):
        document.pop("generated_at")
        document["health"].pop("elapsed_s", None)
    assert served == direct
    assert served["health"]["state"] == "finished" and served["progress"]["improvement"]["pct"] == 10.0


def test_the_root_serves_the_page_itself(server):
    status, content_type, body = _get(server, "")
    assert status == 200 and content_type.startswith("text/html")
    # Bytes, not `read_text`: a checkout that converts line endings serves CRLF,
    # and universal newlines would make the file on disk look different from it.
    assert body == PAGE.read_bytes().decode("utf-8")


def test_a_candidate_is_served_with_its_code_and_its_diff(server):
    status, _type, body = _get(server, "api/candidate/g001-c0002")
    detail = json.loads(body)
    assert status == 200 and detail["block"] == HOSTILE and detail["parent_id"] == "g000-c0001"
    assert "-x = 1" in detail["diff_vs_parent"]
    assert _get(server, "api/candidate/nope")[0] == 404
    assert _get(server, "api/nothing")[0] == 404


# -- it may read the run directory, and nothing else -----------------------


def test_a_log_of_the_run_is_one_click_away(server):
    status, content_type, body = _get(server, "api/log?path=work/stage_out/g001-c0003.full.stderr.log")
    assert status == 200 and content_type.startswith("text/plain") and "Segmentation fault" in body


@pytest.mark.parametrize(
    "path",
    ["../secret.log", "..%2Fsecret.log", "work/../../secret.log", "C:/Windows/win.ini", "/etc/passwd",
     "runs.jsonl/../../secret.log", "", "work/stage_out", ".lock"],
)
def test_nothing_outside_the_run_directory_can_be_read(server, path):
    status, _type, body = _get(server, "api/log?path=" + path)
    assert status == 404 and "not part of the run" not in body


@pytest.mark.parametrize(
    "path",
    ["\\\\10.255.255.1\\share\\x.log", "//10.255.255.1/share/x.log", "\\\\?\\C:\\Windows\\win.ini",
     "C:win.ini", "C:\\Windows\\win.ini", "/etc/passwd", "../secret.log", "work\\..\\..\\secret.log"],
)
def test_a_path_outside_the_run_is_refused_before_the_filesystem_is_asked(run_dir, path, monkeypatch):
    """Resolving `\\\\host\\share\\x.log` on Windows opens an SMB connection to
    `host` -- with the user's credentials -- before any check can say no, and a
    web page can make the browser request that URL from the local dashboard. So
    the path is judged as text first, and only a path inside the run directory
    is ever handed to the filesystem."""
    from evolvekit import dashboard

    root = run_dir.resolve()
    touched: list[str] = []
    real_resolve = Path.resolve

    def resolve(self, *args, **kwargs):
        touched.append(str(self))
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert dashboard._safe_path(run_dir, path) is None
    outside = [p for p in touched if not p.startswith(str(root)) and p != str(run_dir)]
    assert outside == [], f"asked the filesystem about {outside}"


def _get_with_headers(server: DashboardServer, path: str, headers: dict) -> int:
    import http.client

    host, port = server._server.server_address[:2]
    connection = http.client.HTTPConnection(host, port, timeout=10)
    try:
        connection.putrequest("GET", "/" + path, skip_host=True)
        for name, value in headers.items():
            connection.putheader(name, value)
        connection.endheaders()
        return connection.getresponse().status
    finally:
        connection.close()


def test_a_page_on_another_site_cannot_read_the_dashboard(server):
    """DNS rebinding: a hostile page points its own name at 127.0.0.1 and reads
    `/api/status` -- the code, the command lines, the paths -- as same-origin.
    The request then carries that name in `Host`, which a server bound to this
    machine can refuse. A plain cross-site request says so in `Sec-Fetch-Site`."""
    port = server._server.server_address[1]
    assert _get_with_headers(server, "api/status", {"Host": f"127.0.0.1:{port}"}) == 200
    assert _get_with_headers(server, "api/status", {"Host": f"localhost:{port}"}) == 200
    assert _get_with_headers(server, "api/status", {"Host": f"attacker.example:{port}"}) == 403
    assert _get_with_headers(server, "api/status", {}) == 403
    assert _get_with_headers(
        server, "api/log?path=runs.jsonl",
        {"Host": f"127.0.0.1:{port}", "Sec-Fetch-Site": "cross-site"},
    ) == 403
    assert _get_with_headers(
        server, "api/status", {"Host": f"127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"}
    ) == 200


def test_it_only_listens_on_this_machine_unless_told_otherwise(server):
    assert server.url.startswith("http://127.0.0.1:")


def test_two_dashboards_asking_for_one_port_get_two_ports(run_dir):
    """`HTTPServer` sets SO_REUSEADDR, which on Windows lets a second server
    bind a port that is in use -- after which either one answers."""
    first = DashboardServer(run_dir, port=18765)
    second = DashboardServer(run_dir, port=18765)
    try:
        assert first.url != second.url
    finally:
        first.stop()
        second.stop()


# -- no network, no build step ---------------------------------------------


def test_the_page_never_reaches_for_the_network():
    page = PAGE.read_text(encoding="utf-8")
    external = [u for u in re.findall(r"https?://[^\s\"'<>)]+", page) if "www.w3.org" not in u]
    assert external == [], "an offline machine must be able to render every pixel"
    assert "<link rel=\"stylesheet\"" not in page and "src=\"http" not in page


def test_run_text_only_reaches_the_page_as_text():
    """Candidate source and stderr are untrusted. The page builds DOM nodes and
    sets text; it never assembles markup from strings."""
    page = PAGE.read_text(encoding="utf-8")
    assert "innerHTML" not in page.replace("never through innerHTML", "")
    assert "insertAdjacentHTML" not in page and "document.write" not in page


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to parse the page's script")
def test_the_inline_script_parses():
    page = PAGE.read_text(encoding="utf-8")
    script = page[page.index("<script>") + len("<script>") : page.rindex("</script>")]
    check = subprocess.run(
        ["node", "-e", "new Function(require('fs').readFileSync(0, 'utf8'))"],
        input=script, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert check.returncode == 0, check.stderr[-800:]


# -- the exported file -----------------------------------------------------


def test_the_export_is_one_file_with_the_document_inside(run_dir, tmp_path):
    out = export_html(run_dir, tmp_path / "out" / "report.html")
    text = out.read_text(encoding="utf-8")
    template = PAGE.read_text(encoding="utf-8")
    assert "/*__EVOLVEKIT_EMBEDDED__*/null" in template and "/*__EVOLVEKIT_EMBEDDED__*/null" not in text
    start = text.index("const EMBEDDED = ") + len("const EMBEDDED = ")
    embedded = json.loads(text[start : text.index(";\n", start)])
    assert embedded["status"]["health"]["state"] == "finished"
    assert embedded["candidates"]["g001-c0002"]["block"] == HOSTILE


def test_a_candidate_cannot_close_the_script_element_of_the_export(run_dir, tmp_path):
    text = export_html(run_dir, tmp_path / "report.html").read_text(encoding="utf-8")
    template = PAGE.read_text(encoding="utf-8")
    assert text.count("</script>") == template.count("</script>")
    assert "<script>alert(1)" not in text


# -- the command line ------------------------------------------------------


def test_dashboard_export_from_the_command_line(run_dir, tmp_path, capsys):
    target = tmp_path / "report.html"
    assert main(["dashboard", "--run-dir", str(run_dir), "--export", str(target)]) == 0
    assert target.is_file() and "wrote" in capsys.readouterr().out


def test_dashboard_on_a_directory_that_does_not_exist_is_an_error(tmp_path, capsys):
    assert main(["dashboard", "--run-dir", str(tmp_path / "typo")]) == 1
    assert "no run directory" in capsys.readouterr().err
    assert not (tmp_path / "typo").exists()


# -- a live run has an archive to show -------------------------------------


@pytest.mark.slow
def test_the_archive_snapshot_is_refreshed_every_generation_not_only_at_the_end(tmp_path, monkeypatch):
    """It used to be written once, when a session ended: blank for the whole of
    a live run, and never written by a run that was killed."""
    from dataclasses import replace

    from evolvekit.config import load_config
    from evolvekit.ledger import Ledger
    from evolvekit.search.driver import Driver
    from tests.conftest import EXAMPLE_CONFIG

    seen: list[int] = []
    real = Ledger.write_archive

    def counting(self, payload):
        seen.append(payload["counts"]["members"])
        return real(self, payload)

    monkeypatch.setattr(Ledger, "write_archive", counting)
    config = load_config(EXAMPLE_CONFIG)
    config = replace(config, search=replace(config.search, scratchpad_every=0))
    Driver(config, run_dir=tmp_path / "run").run(generations=2)
    assert len(seen) >= 3, "one snapshot per generation (the seed's included)"
    assert seen[0] == 1 and seen[-1] > 1


def test_css_custom_properties_reach_the_element():
    """`Object.assign(node.style, {"--zero": ...})` drops a custom property
    without a word -- the per-instance bars lost their zero line that way and
    grew out of the page when every instance was a win -- so the element helper
    has to route `--*` through `setProperty`."""
    page = PAGE.read_text(encoding="utf-8")
    assert 'style: { "--' in page, "no custom property is passed any more: this test can go"
    assert "node.style.setProperty(" in page


def test_an_absent_block_is_not_the_word_null():
    """`h()` skips a `null` child; `replaceChildren` does not -- it renders the
    text "null". The health card returns `null` for the host-load warning when
    the machine was the run's alone, and the second real run showed the word
    where the warning would have been."""
    page = PAGE.read_text(encoding="utf-8")
    assert "function fill(id, kids) { $(id).replaceChildren(...[kids].flat().filter((kid) => kid != null && kid !== false)); }" in page
    assert "$(\"health\").replaceChildren(" not in page, "the health card is redrawn every tick through its own call: it has to go through `fill` too"
