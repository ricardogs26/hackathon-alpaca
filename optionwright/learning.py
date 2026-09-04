"""`python -m optionwright.learning` — the nightly statistical memory (see agent/learning.py)."""
from optionwright.agent.learning import run_nightly  # noqa: F401

if __name__ == "__main__":
    import runpy

    runpy.run_module("optionwright.agent.learning", run_name="__main__")
