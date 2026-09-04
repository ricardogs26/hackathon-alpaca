"""optionwright — the LLM proposes, the code decides.

`__version__` is the single source of truth for the release version. It is
exposed by /health and /api/status, shown in the dashboard footer, and used by
`make release` as the image tag, so code, container and git tag always agree.
"""
__version__ = "0.5.1"
