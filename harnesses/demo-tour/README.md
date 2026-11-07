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

**Tables:** `stops` (the request), `tour` (one row per visit, with the leg
from the previous stop) and `summary` (lengths and the moves tried).
