"""evolvekit — an LLM-driven evolutionary program-search loop.

Built in three layers (still visible as "Phase A/B/C" in older comments):
candidate representation, staged evaluation, cost ledger, stop policy and the
provider abstraction; then the MAP-Elites archive, mutation operators,
small/strong routing, the structural and behavioural novelty filters, and the
meta-scratchpad; then the near-duplicate gate, the USD-per-improvement view
and its two stop rules, adaptive breadth, and the circle-packing example.

See README.md and docs/new-experiment.md.
"""

__version__ = "0.3.0"

__all__ = ["__version__"]
