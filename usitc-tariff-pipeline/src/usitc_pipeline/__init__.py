"""Reproducible USITC DataWeb tariff-research pipeline.

The public package deliberately exposes only the stable configuration loader and
version.  Research notebooks, report builders, and slide builders should import
the named functions from the relevant modules instead of copying algorithms.
"""

from .config import ResearchConfig, load_config

__all__ = ["ResearchConfig", "load_config"]
__version__ = "0.2.0"

