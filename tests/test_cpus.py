"""The core layout a tuning run's workers are planned from (`evolvekit/cpus.py`)."""

from __future__ import annotations

import os

from evolvekit import cpus


def test_every_logical_cpu_belongs_to_exactly_one_core():
    cores = cpus.physical_cores()
    logical = [cpu for core in cores for cpu in core]
    assert cores and all(core for core in cores)
    assert sorted(logical) == sorted(set(logical))
    assert len(logical) <= (os.cpu_count() or len(logical))
    assert cores == sorted(cores, key=min), "core 0 first"


def test_one_worker_per_core_but_the_first(monkeypatch):
    monkeypatch.setattr(cpus, "can_pin", lambda: True)
    four_by_two = [[0, 1], [2, 3], [4, 5], [6, 7]]
    assert cpus.plan_workers(100, cores=four_by_two) == (3, [2, 4, 6])
    assert cpus.plan_workers(2, cores=four_by_two) == (2, [2, 4]), "never more workers than runs"
    assert cpus.plan_workers(100, cores=[[0], [1], [2], [3]]) == (3, [1, 2, 3])


def test_a_single_core_still_gets_one_worker(monkeypatch):
    monkeypatch.setattr(cpus, "can_pin", lambda: True)
    assert cpus.plan_workers(10, cores=[[0, 1]]) == (1, [])
    assert cpus.plan_workers(0, cores=[[0], [1], [2]]) == (1, [1])


def test_no_pinning_where_the_platform_cannot_pin(monkeypatch):
    monkeypatch.setattr(cpus, "can_pin", lambda: False)
    assert cpus.plan_workers(100, cores=[[0, 1], [2, 3], [4, 5]]) == (2, [])


def test_linux_layout_is_read_from_sysfs(tmp_path, monkeypatch):
    for cpu, (package, core) in enumerate([(0, 0), (0, 1), (0, 0), (0, 1)]):
        topology = tmp_path / f"cpu{cpu}" / "topology"
        topology.mkdir(parents=True)
        (topology / "physical_package_id").write_text(f"{package}\n")
        (topology / "core_id").write_text(f"{core}\n")
    monkeypatch.setattr(cpus, "_SYSFS", tmp_path)
    assert cpus._linux() == [[0, 2], [1, 3]]
