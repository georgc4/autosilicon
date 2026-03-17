"""AutoSilicon — Shared Testbench Utilities for cocotb Tests.

Design-agnostic fixed-point, assertion, clock, and reset helpers.
Adapted from the NLT accelerator's nlt_tb_utils.py.

Usage in design-specific test utilities:
    from infra.tb_utils import to_fixed, from_fixed, assert_close, start_clock, reset_dut
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles
import logging


# ---------------------------------------------------------------------------
# Fixed-point conversion helpers (design-agnostic)
# ---------------------------------------------------------------------------

def to_fixed(value: float, frac_bits: int, total_bits: int = 32) -> int:
    """Convert float to signed fixed-point integer (truncation, matching RTL).

    Args:
        value: Float value to convert.
        frac_bits: Number of fractional bits.
        total_bits: Total word width (default 32).

    Returns:
        Signed fixed-point integer, clamped to [min_int, max_int].
    """
    import math
    scale = 1 << frac_bits
    scaled = int(math.floor(value * scale))
    max_int = (1 << (total_bits - 1)) - 1
    min_int = -(1 << (total_bits - 1))
    return max(min_int, min(max_int, scaled))


def from_fixed(value: int, frac_bits: int, total_bits: int = 32) -> float:
    """Convert signed fixed-point integer to float.

    Handles sign extension from total_bits.

    Args:
        value: Raw integer (possibly unsigned from RTL).
        frac_bits: Number of fractional bits.
        total_bits: Total word width (default 32).

    Returns:
        Float representation.
    """
    v = int(value)
    if v >= (1 << (total_bits - 1)):
        v -= (1 << total_bits)
    return v / (1 << frac_bits)


def to_signed(unsigned_val: int, width: int) -> int:
    """Convert an unsigned N-bit value (from RTL) to signed Python int.

    Args:
        unsigned_val: Unsigned integer value.
        width: Bit width of the value.

    Returns:
        Signed integer.
    """
    v = int(unsigned_val) & ((1 << width) - 1)
    if v >= (1 << (width - 1)):
        v -= (1 << width)
    return v


def to_unsigned(signed_val: int, width: int) -> int:
    """Convert a signed Python int to unsigned N-bit representation for RTL.

    Args:
        signed_val: Signed integer value.
        width: Bit width.

    Returns:
        Unsigned integer suitable for driving RTL signals.
    """
    if signed_val < 0:
        return signed_val + (1 << width)
    return signed_val & ((1 << width) - 1)


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

def assert_close(
    actual: float, expected: float,
    rtol: float = 0.001, atol: float = 1e-6,
    msg: str = "",
) -> None:
    """Assert two float values are close (relative or absolute tolerance).

    Uses absolute tolerance when expected is near zero, relative otherwise.

    Args:
        actual: Actual value.
        expected: Expected value.
        rtol: Relative tolerance (default 0.1%).
        atol: Absolute tolerance for near-zero values.
        msg: Optional message for assertion error.
    """
    if abs(expected) < atol * 10:
        diff = abs(actual - expected)
        assert diff <= atol, (
            f"Absolute mismatch: actual={actual}, expected={expected}, "
            f"diff={diff}, atol={atol} {msg}"
        )
    else:
        rel = abs(actual - expected) / abs(expected)
        assert rel <= rtol, (
            f"Relative mismatch: actual={actual}, expected={expected}, "
            f"rel_err={rel:.6e}, rtol={rtol} {msg}"
        )


def assert_close_ulp(
    actual_fixed: int, expected_fixed: int,
    ulps: int = 2, width: int = 32,
    msg: str = "",
) -> None:
    """Assert two fixed-point integers are within N ULPs of each other."""
    diff = abs(to_signed(actual_fixed, width) - to_signed(expected_fixed, width))
    assert diff <= ulps, (
        f"ULP mismatch: actual=0x{actual_fixed:08x}, expected=0x{expected_fixed:08x}, "
        f"diff={diff} ULPs, max={ulps} {msg}"
    )


# ---------------------------------------------------------------------------
# Clock and reset helpers
# ---------------------------------------------------------------------------

def start_clock(dut, period_ns: int = 20):
    """Start a clock on dut.clk with given period (default 20ns = 50MHz).

    Args:
        dut: cocotb DUT handle.
        period_ns: Clock period in nanoseconds.

    Returns:
        cocotb task handle for the clock.
    """
    return cocotb.start_soon(Clock(dut.clk, period_ns, unit="ns").start())


async def reset_dut(dut, cycles: int = 5):
    """Assert rst_n low for N cycles, then release.

    Assumes clock is already running.

    Args:
        dut: cocotb DUT handle.
        cycles: Number of clock cycles to hold reset.
    """
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, cycles)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------

def get_logger(name: str) -> logging.Logger:
    """Get a logger configured for cocotb output."""
    return logging.getLogger(f"cocotb.tb.{name}")
