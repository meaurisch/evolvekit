"""Harnesses and studies: evolvekit for people who do not write configs.

An expert writes a *harness* once per application -- the plumbing around it
(`runner.py` on top of the SDK), and a vocabulary of tables, settings, data
changes (levers), KPIs and study templates (`harness.yaml`). A consultant
makes a *study* per analysis: what may change, under which constraints, what
counts as success, and how much time to spend (`study.yaml`). This package
compiles a harness and a study into an ordinary `evolvekit.yaml` and runs it;
the engine does not know harnesses exist.
"""


class HarnessError(ValueError):
    """A harness or a study that cannot be used, said in one sentence that
    names the key path of the mistake."""
