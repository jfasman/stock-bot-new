"""Shared types for the setup library. Lives in its own module so each
setup file can import without creating a cycle through `__init__`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class OptionsContext:
    """Compressed options state for setup matching. Built by the
    caller (typically the matcher in the assembler) from
    `data/options.py` reads. Setups that don't care about options
    ignore this and accept `None`.
    """
    iv_rank: Optional[float]                 # 0..100; None when chain unavailable
    nearest_expiration_dte: Optional[int]    # days to nearest listed expiration
    has_catalyst_within_dte: bool            # earnings or other scheduled event
