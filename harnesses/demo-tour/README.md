# Demo tour

A small travelling-salesman solver with a command line — evolvekit's example
solver, `examples/cli-solver/solver.py` — behind the harness SDK. It needs
nothing but Python and runs in seconds, which makes it the harness to try the
app on, and the one the tests use.

**The application** is the solver program itself: point the study at
`examples/cli-solver/solver.py` in the evolvekit folder. A `.py` program is run
with the Python that runs evolvekit.

**Cases** are tour requests: a JSON file with the stops in kilometres,
`"stops": [[x, y, weight], ...]` (and optionally `"tags": {"0": "north", ...}`),
or the example's own `{"seed": ..., "cities": ...}`. `samples/` holds four,
from 30 to 60 stops; `samples/make_samples.py` regenerates them.

**What may change**

- five solver settings (four recommended for a first study);
- stop weights: the solver minimises the *weighted* length, so a weight other
  than 1 changes which tour it prefers. Always judge a weight study by
  `tour_length` (the plain length), never by `solver_length` (what the solver
  saw) — `harness check` warns when a template does.

**Code:** a code study may also change the solver's own source. `code:` in
`harness.yaml` names it: `repo: ../../examples/cli-solver` (relative to this
folder, so inside the evolvekit repository; a study records its own absolute
path), `open: [solver.py]`, and `app: "{tree}/solver.py"`, the program inside
each combination's checkout. There is no build: each combination runs its own
copy of `solver.py`. `harness check` makes a clean checkout of `HEAD` in a
scratch folder and solves a sample from it. `evolvekit harness new --from
demo-tour` keeps `code.repo` pointing at the solver's source (it writes the
absolute path). A copy made by hand, or a packed harness installed elsewhere,
does not: point its `code.repo` at the solver's source again.

**Tables:** `stops` (the request), `tour` (one row per visit, with the leg
from the previous stop) and `summary` (lengths and the moves tried).
