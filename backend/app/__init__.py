"""VGOH backend — provider-independent AI video generation harness.

This package implements the core orchestration described in the project README.
Provider integrations are isolated under ``app.providers``; the core never depends
on a provider-specific session or conversation.
"""

__version__ = "0.1.0"
