"""The novelty filter: never pay to evaluate something you already evaluated.

Evidence from the first real run (2026-08-22, $0.45, 12 paid calls): the small
model produced worst-fit three separate times -- three identical scores of
-14.48 -- and re-expressed best-fit as itself twice. About 40 % of the paid
calls bought nothing. Both failure modes are textual variation over an
identical program, so the filter works on the AST, not on the characters:

1. parse the block;
2. drop docstrings (comments are already gone -- they never reach the AST);
3. canonicalise local identifiers, in order of first appearance, to `v0`, `v1`
   ...  Top-level function and class names are left alone: they are the
   skeleton's contract, and two blocks that differ only in which function they
   define are genuinely different candidates;
4. hash the dumped tree.

A child whose hash equals its parent's is a **no-op**; one that equals any
archived candidate's is a **duplicate**. Both are recorded with `rejected=true`
and a `reject_reason` naming the twin, and neither is ever evaluated.

A block that will not parse gets a whitespace-normalised text hash instead --
it is about to be hard-rejected by the static stage anyway, and a filter that
raises is worse than a filter that occasionally misses.

## Near-duplicates (Phase C)

The exact hash above is all-or-nothing, and the second real run showed what it
misses: "best fit, but the bonus constant is 18 instead of 20" is a different
structure, a different hash, and a full evaluation nobody wanted to buy. So a
*similarity* gate sits between the exact hash and the evaluator.

Two methods, chosen by `search.novelty.near.method`:

* `local` (default, and always available) -- cosine similarity over the
  multiset of node-type n-grams of the **canonicalised** AST. Node *types*
  only: `Constant` is `Constant` whatever number it holds, so a block that
  differs from an earlier one in nothing but its constants scores exactly 1.0.
  Costs nothing and calls nothing.
* `embedding` -- cosine over vectors from `Provider.embed`, for the case where
  a semantic judgement beats a syntactic one. Costs one embedding call per
  child, recorded in the ledger like any other spend.
* `off` -- the Phase B behaviour.

A near-duplicate is *not* rejected. It gets the same one re-prompt a structural
duplicate gets, and if the retry is still near it is **evaluated anyway** and
flagged (`novelty="near"`, `near_twin_id`, `similarity`). A filter that can
starve the search is worse than a filter that occasionally pays for a cousin --
run 2 found nothing in 12 novel samples, and the answer to that is more
sampling, not more rejection.
"""

from __future__ import annotations

import ast
import hashlib
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Mapping, Protocol, Sequence

from evolvekit.candidate import block_hash
from evolvekit.providers.base import ProviderError, embedding_tokens

if TYPE_CHECKING:  # pragma: no cover - typing only
    from evolvekit.config import NearDuplicateConfig

__all__ = [
    "normalise_block",
    "novelty_hash",
    "node_type_sequence",
    "node_ngrams",
    "cosine",
    "local_similarity",
    "NearBackend",
    "LocalNearIndex",
    "EmbeddingNearIndex",
    "build_near_backend",
    "NoveltyIndex",
    "NoveltyVerdict",
    "NGRAM_SIZE",
]

NGRAM_SIZE = 3
"""Length of the node-type n-grams the local method compares. Three is short
enough that a two-line edit still moves the vector and long enough that the
comparison is about shape rather than about which node types appear at all."""


class _Canonicaliser(ast.NodeTransformer):
    """Rename locals to `v0`, `v1`, ... and delete docstrings."""

    def __init__(self, protected: set[str]) -> None:
        self.protected = protected
        self.mapping: dict[str, str] = {}

    def _rename(self, name: str) -> str:
        if name in self.protected or name.startswith("__"):
            return name
        if name not in self.mapping:
            self.mapping[name] = f"v{len(self.mapping)}"
        return self.mapping[name]

    # -- docstrings ------------------------------------------------------

    def _strip_docstring(self, node: ast.AST) -> None:
        body = getattr(node, "body", None)
        if not body:
            return
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            body.pop(0)
            if not body:
                body.append(ast.Pass())

    def visit_Module(self, node: ast.Module) -> ast.AST:
        self._strip_docstring(node)
        return self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        self._strip_docstring(node)
        return self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        self._strip_docstring(node)
        return self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        self._strip_docstring(node)
        return self.generic_visit(node)

    # -- identifiers -----------------------------------------------------

    def visit_arg(self, node: ast.arg) -> ast.AST:
        node.arg = self._rename(node.arg)
        node.annotation = None
        return self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> ast.AST:
        node.id = self._rename(node.id)
        return node


def _protected_names(tree: ast.Module) -> set[str]:
    """Top-level definitions plus every imported name -- the block's interface."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def normalise_block(block: str) -> str:
    """The block's canonical form: no docstrings, no comments, no local names."""
    try:
        tree = ast.parse(block)
    except SyntaxError:
        return "\n".join(
            line.rstrip() for line in block.strip().splitlines() if line.strip()
        )
    transformer = _Canonicaliser(_protected_names(tree))
    canonical = ast.fix_missing_locations(transformer.visit(tree))
    return ast.dump(canonical, annotate_fields=True, include_attributes=False)


def novelty_hash(block: str) -> str:
    """A 12-hex fingerprint of the block's *structure*."""
    normalised = normalise_block(block)
    if not normalised.startswith("Module("):
        # Unparseable: fall back to the plain text fingerprint, which at least
        # still catches a byte-identical repeat.
        return block_hash(block)
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------
# near-duplicates: similarity, not equality
# --------------------------------------------------------------------------


def node_type_sequence(block: str) -> list[str]:
    """Pre-order DFS of the canonicalised AST, as node *type* names.

    Types only. Two blocks that differ in nothing but their numeric constants
    produce the same sequence, which is exactly the case the exact hash misses
    and this section exists to catch.
    """
    try:
        tree = ast.parse(block)
    except SyntaxError:
        return []
    transformer = _Canonicaliser(_protected_names(tree))
    canonical = ast.fix_missing_locations(transformer.visit(tree))
    out: list[str] = []
    stack: list[ast.AST] = [canonical]
    while stack:
        node = stack.pop()
        out.append(type(node).__name__)
        stack.extend(reversed(list(ast.iter_child_nodes(node))))
    return out


def node_ngrams(block: str, n: int = NGRAM_SIZE) -> dict[tuple[str, ...], int]:
    """The multiset of node-type n-grams. Empty for a block that will not parse.

    A block with fewer than `n` nodes contributes its whole sequence as one
    gram rather than nothing, so two one-liners are still comparable.
    """
    types = node_type_sequence(block)
    if not types:
        return {}
    if len(types) < n:
        return {tuple(types): 1}
    return dict(Counter(tuple(types[i : i + n]) for i in range(len(types) - n + 1)))


def cosine(a: Mapping[object, float], b: Mapping[object, float]) -> float:
    """Cosine similarity of two sparse count vectors; 0.0 when either is empty."""
    if not a or not b:
        return 0.0
    dot = sum(float(count) * float(b[key]) for key, count in a.items() if key in b)
    norm_a = math.sqrt(sum(float(v) * float(v) for v in a.values()))
    norm_b = math.sqrt(sum(float(v) * float(v) for v in b.values()))
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (norm_a * norm_b)))


def _cosine_dense(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(float(x) * float(y) for x, y in zip(a, b))
    norm_a = math.sqrt(sum(float(x) * float(x) for x in a))
    norm_b = math.sqrt(sum(float(y) * float(y) for y in b))
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (norm_a * norm_b)))


def local_similarity(block_a: str, block_b: str, n: int = NGRAM_SIZE) -> float:
    """Zero-cost structural similarity of two blocks, in [0, 1]."""
    return cosine(node_ngrams(block_a, n), node_ngrams(block_b, n))


class NearBackend(Protocol):
    """Anything that can answer "what have I seen that is closest to this?"."""

    method: str

    def add(self, candidate_id: str, block: str) -> None: ...

    def closest(self, block: str) -> tuple[str | None, float]: ...


@dataclass
class LocalNearIndex:
    """Node-type n-gram cosine against everything the run has kept.

    Linear in the number of archived candidates and microseconds per
    comparison; at the 10^2-10^3 candidate scale this project targets, that is
    cheaper than any bookkeeping to avoid it would be.
    """

    method: str = "local"
    n: int = NGRAM_SIZE
    vectors: dict[str, dict[tuple[str, ...], int]] = field(default_factory=dict)

    def add(self, candidate_id: str, block: str) -> None:
        grams = node_ngrams(block, self.n)
        if grams:
            self.vectors.setdefault(candidate_id, grams)

    def closest(self, block: str) -> tuple[str | None, float]:
        grams = node_ngrams(block, self.n)
        if not grams:
            return None, 0.0
        best_id, best = None, 0.0
        for candidate_id, other in self.vectors.items():
            score = cosine(grams, other)
            if score > best:
                best_id, best = candidate_id, score
        return best_id, best


@dataclass
class EmbeddingNearIndex:
    """Cosine over `Provider.embed` vectors. One embedding call per new block.

    Vectors are cached by structural hash, so a re-prompt that came back
    byte-identical costs nothing and a resumed run embeds each archived block
    exactly once.
    """

    provider: object
    model: str
    on_call: Callable[[int, int], None] | None = None
    """Called `(input_tokens, texts)` after every embedding request so the
    driver can bill it. A callback rather than a ledger reference: this module
    knows nothing about run directories."""
    method: str = "embedding"
    vectors: dict[str, list[float]] = field(default_factory=dict)
    cache: dict[str, list[float]] = field(default_factory=dict)

    def _embed(self, block: str) -> list[float]:
        digest = novelty_hash(block)
        cached = self.cache.get(digest)
        if cached is not None:
            return cached
        vectors = self.provider.embed([block], model=self.model)  # type: ignore[attr-defined]
        if not vectors or not vectors[0]:
            raise ProviderError(
                f"embedding provider {getattr(self.provider, 'name', '?')!r} "
                "returned no vector"
            )
        vector = [float(v) for v in vectors[0]]
        self.cache[digest] = vector
        if self.on_call is not None:
            self.on_call(embedding_tokens(self.provider, [block]), 1)
        return vector

    def add(self, candidate_id: str, block: str) -> None:
        if candidate_id not in self.vectors:
            self.vectors[candidate_id] = self._embed(block)

    def closest(self, block: str) -> tuple[str | None, float]:
        if not self.vectors:
            return None, 0.0
        vector = self._embed(block)
        best_id, best = None, 0.0
        for candidate_id, other in self.vectors.items():
            score = _cosine_dense(vector, other)
            if score > best:
                best_id, best = candidate_id, score
        return best_id, best


def build_near_backend(
    config: "NearDuplicateConfig",
    *,
    provider: object | None = None,
    on_call: Callable[[int, int], None] | None = None,
) -> "NearBackend | None":
    """`None` when the gate is off; otherwise the configured backend."""
    if config.method == "off":
        return None
    if config.method == "local":
        return LocalNearIndex()
    if provider is None:
        raise ProviderError(
            "search.novelty.near.method is 'embedding' but no embedding "
            "provider was supplied"
        )
    if not hasattr(provider, "embed"):
        raise ProviderError(
            f"provider {getattr(provider, 'name', '?')!r} does not implement "
            "embed(); use search.novelty.near.method: local"
        )
    return EmbeddingNearIndex(provider=provider, model=config.model, on_call=on_call)


@dataclass(frozen=True)
class NoveltyVerdict:
    """`kind` is None (novel), `"no_op"`, `"duplicate"` or `"near"`.

    The first three are decided by the exact structural hash and are hard
    rejections. `"near"` is decided by the similarity gate and is *not*: it
    carries `similarity` and is a flag on a candidate that still gets
    evaluated.
    """

    kind: str | None = None
    twin_id: str | None = None
    similarity: float | None = None

    @property
    def novel(self) -> bool:
        return self.kind is None

    @property
    def near(self) -> bool:
        return self.kind == "near"

    @property
    def rejects(self) -> bool:
        """Whether this verdict means "do not evaluate"."""
        return self.kind in ("no_op", "duplicate")

    @property
    def reason(self) -> str | None:
        if self.kind == "no_op":
            return f"no-op: structurally identical to its parent {self.twin_id}"
        if self.kind == "duplicate":
            return f"duplicate: structurally identical to {self.twin_id}"
        if self.kind == "near":
            return (
                f"near-duplicate: closest to {self.twin_id} at similarity "
                f"{self.similarity:.4g}"
            )
        return None

    @property
    def retry_hint(self) -> str:
        if self.kind == "near":
            return (
                f"Your previous answer was closest to {self.twin_id} at "
                f"similarity {self.similarity:.2f} -- change the decision "
                "rule, not the constants."
            )
        return (
            f"Your previous answer was structurally identical to {self.twin_id} "
            "(same code once docstrings, comments and variable names are "
            "normalised away). Propose something genuinely different: change "
            "the rule, not its wording."
        )


@dataclass
class NoveltyIndex:
    """Every structure the run has already seen, mapped to the first id.

    `near` is the optional similarity gate. It sits *after* the exact hash --
    there is no point measuring how close something is to a twin it already is
    -- and *before* the evaluator.
    """

    by_hash: dict[str, str] = field(default_factory=dict)
    near: "NearBackend | None" = None
    threshold: float = 0.97

    def add(self, candidate_id: str, block: str) -> str:
        digest = novelty_hash(block)
        self.by_hash.setdefault(digest, candidate_id)
        if self.near is not None:
            self.near.add(candidate_id, block)
        return digest

    def lookup(self, block: str) -> str | None:
        return self.by_hash.get(novelty_hash(block))

    def closest(self, block: str) -> tuple[str | None, float]:
        """The nearest thing the run has kept, and how near. `(None, 0.0)` when
        the gate is off or nothing has been indexed yet."""
        if self.near is None:
            return None, 0.0
        return self.near.closest(block)

    def check(
        self,
        block: str,
        parent_block: str,
        parent_id: str,
        *,
        near: bool = True,
    ) -> NoveltyVerdict:
        """Classify a proposed child before anyone spends a second on it.

        `near=False` runs the exact checks only. The driver passes it for
        `param_lhs`, whose entire job is to vary constants and nothing else:
        flagging a parameter sweep as "too similar, you only changed the
        constants" would be a category error, and on the embedding method it
        would also buy a vector for a child no model wrote.
        """
        digest = novelty_hash(block)
        if digest == novelty_hash(parent_block):
            return NoveltyVerdict("no_op", parent_id)
        twin = self.by_hash.get(digest)
        if twin is not None:
            return NoveltyVerdict("duplicate", twin)
        if not near:
            return NoveltyVerdict()
        twin_id, similarity = self.closest(block)
        if twin_id is not None and similarity >= self.threshold:
            return NoveltyVerdict("near", twin_id, similarity)
        return NoveltyVerdict()
