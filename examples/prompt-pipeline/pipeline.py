"""prompt-pipeline: a small example of an application whose behaviour lives in
a prompt file.

It reads an evaluation item (a JSON file with an `input` and an `expected`
answer), builds a prompt from `prompts/system.md` (beside this file) and the
input, asks a provider for an answer, and prints the answer's score as one line
of JSON:

    python pipeline.py --item items/01-capital.json
    {"score": 0.119}

The only provider built in is `fake` (the default; `EVOLVEKIT_PROMPT_PROVIDER`
names another): it needs no network and no key, and scores by hashing the
prompt with the input, so the same prompt always gets the same score and a
changed prompt gets another one. It stands in for a real model and a real
grader; the evolvekit tests use it.

The fake score is a hash, not a measure of prompt quality.
"""

import argparse
import hashlib
import json
import os
import sys

VERSION = "1.0"
HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT = os.path.join(HERE, "prompts", "system.md")


def read_prompt(path=PROMPT):
    # Text mode: a checkout with CRLF line endings reads the same prompt as one with LF.
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def fake_score(prompt, item):
    """The fake provider and grader in one: deterministic, between 0 and 1."""
    digest = hashlib.sha256((prompt + "\n" + item["input"]).encode()).hexdigest()
    return int(digest[:8], 16) % 1000 / 1000


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pipeline.py", description="Score one evaluation item.")
    parser.add_argument("--item", help="the evaluation item, a JSON file with `input` and `expected`")
    parser.add_argument("--version", action="store_true", help="print the version and stop")
    args = parser.parse_args(argv)
    if args.version:
        print("prompt-pipeline " + VERSION)
        return 0
    if not args.item:
        parser.error("give --item PATH")
    provider = os.environ.get("EVOLVEKIT_PROMPT_PROVIDER", "fake") or "fake"
    if provider != "fake":
        print("prompt-pipeline: only the fake provider is built in", file=sys.stderr)
        return 2
    with open(args.item, "r", encoding="utf-8") as handle:
        item = json.load(handle)
    if not isinstance(item, dict) or not isinstance(item.get("input"), str):
        print("prompt-pipeline: an item is a JSON object with an `input` text", file=sys.stderr)
        return 2
    print(json.dumps({"score": fake_score(read_prompt(), item)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
