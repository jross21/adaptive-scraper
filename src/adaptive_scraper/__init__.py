"""Adaptive self-writing scraper.

The output schema is the fixed contract; an LLM writes the per-site extraction
logic (an ExtractionSpec) at runtime, which a deterministic interpreter then runs.
Phase 1 ("prove the loop"): single URL -> compressed DOM -> codegen -> interpret
-> validate -> structured output. No cache, no repair loop, no sandbox.
"""

__version__ = "0.1.0"
