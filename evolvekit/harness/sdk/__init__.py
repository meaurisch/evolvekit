"""The harness SDK, `evk_harness.py`: copied into every harness beside its
`runner.py`, and imported here so that evolvekit shares its table and SQL
code instead of keeping a second copy."""

from evolvekit.resources import package_root

SDK_PATH = package_root() / "harness" / "sdk" / "evk_harness.py"
"""The file `evolvekit harness new` copies into a harness."""
