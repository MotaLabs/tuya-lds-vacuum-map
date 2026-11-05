---
paths:
  - "tests/**"
  - "pyproject.toml"
  - ".github/workflows/mutation.yml"
---

# Mutation testing

- A test written for a survivor is done when mutmut kills that mutant. Run
  `nice -n 10 uv run mutmut run --max-children 4 "<module>.<function>*"` and check that `uv run mutmut results`
  no longer lists it.
- Mutant numbers change between runs. Compare survivors by the diff `uv run mutmut show <name>` prints, not
  by name.
- mutmut drops a test that fails on the unmutated code, and every mutant that test covered then survives.
  When the survivors jump, first compare the tests in `mutants/mutmut-stats.json` (`duration_by_test`) with
  `uv run pytest --collect-only -q`.
- A property test that can fail at random poisons a run: once Hypothesis finds the failure, every later
  mutant that runs that test is killed by it. The `mutation` profile in `tests/conftest.py` (fixed seed, no
  example database) stops the replay; the strategies still must not generate input the test itself cannot
  handle, such as a lone surrogate in text that is encoded as UTF-8.
