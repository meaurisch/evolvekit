# Prompt tune

An example pipeline whose behaviour lives in a prompt file —
`examples/prompt-pipeline/pipeline.py` — behind the harness SDK. It shows that
the "code" a code study changes is any text the application reads: here it is
`prompts/system.md`, not a line of Python.

**The application** is the pipeline program: point the study at
`examples/prompt-pipeline/pipeline.py` in the evolvekit folder. A `.py` program
is run with the Python that runs evolvekit. It reads an evaluation item, builds
a prompt from `prompts/system.md` (beside the program) and the item's input,
asks a provider for the answer and prints its score, `{"score": ...}`, from 0
to 1. The only provider built in is `fake` (the default;
`EVOLVEKIT_PROMPT_PROVIDER` names another, and the pipeline refuses it): it
scores by hashing the prompt with the input, so the same prompt always gets the
same score and a changed prompt another one. It stands in for a real model and
a real grader, needs no network and no key, and is what the tests use.

**Cases** are evaluation items: a JSON file with the question and the answer
expected, `{"input": "...", "expected": "..."}`. `samples/` holds three;
`examples/prompt-pipeline/items/` holds six more to study on.

**What may change:** the system prompt only. There are no settings and no data
changes; the measure is `score`, and higher is the goal.

**Code:** `code:` in `harness.yaml` names the source: `repo:
../../examples/prompt-pipeline` (relative to this folder, so inside the
evolvekit repository; a study records its own absolute path),
`open: [prompts/system.md]`, and `app: "{tree}/pipeline.py"`, the program
inside each combination's checkout, which reads that checkout's copy of the
prompt. There is no build. `harness check` makes a clean checkout of `HEAD` in
a scratch folder and solves a sample from it.

**A study on it** starts from the `improve` template, which changes the prompt
and checks the score on held-back items:

```
python -m evolvekit study new my-prompt --harness prompt-tune --template improve
python -m evolvekit study code my-prompt
python -m evolvekit study start my-prompt
```

`study code` says what may change (the prompt file) and where the source is.
A code study needs AI turned on for the search: its models propose the changes.
The search writes changes to the prompt as diffs, runs the pipeline from each
change's own checkout, and the final check compares the best change with
today's prompt on the held-back items. Only the verdict says whether it helps.

**Tables:** `result`, one row with the answer's score.
