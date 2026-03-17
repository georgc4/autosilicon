"""Cocotb tests for foc_clarke (Clarke transform: abc -> alpha-beta).

Tests against the SPECIFICATION (math.md), not RTL implementation details.
Verifies test vectors from section 6.1, zero input, negative full-scale,
and random vectors. Results are compared bit-exact against the fixed-point model.
"""

import cocotb
from cocotb.triggers import RisingEdge, ClockCycles
import math
import random

from foc_tb_utils import (
    start_clock, reset_dut, get_logger,
    foc_to_fixed, foc_from_fixed, foc_to_unsigned, foc_to_signed,
    assert_close_ulp,
    DATA_W, FRAC_W,
)
from foc_fixed_point import FixedPointConfig, clarke_fixed, to_fixed

LOG = get_logger("clarke")
CFG = FixedPointConfig()

# Clarke transform latency (from microarch: 2 cycles)
CLARKE_LATENCY = 2


async def init_clarke(dut):
    """Initialize Clarke module: start clock, reset, deassert enable."""
    start_clock(dut)
    dut.en.value = 0
    dut.ia.value = 0
    dut.ib.value = 0
    await reset_dut(dut)


async def run_clarke(dut, ia_fp: int, ib_fp: int, timeout: int = 20) -> tuple[int, int]:
    """Drive ia/ib, pulse en, wait for done, return (i_alpha, i_beta)."""
    dut.ia.value = foc_to_unsigned(ia_fp)
    dut.ib.value = foc_to_unsigned(ib_fp)
    dut.en.value = 1
    await RisingEdge(dut.clk)
    dut.en.value = 0

    for _ in range(timeout):
        await RisingEdge(dut.clk)
        if dut.done.value == 1:
            alpha_raw = int(dut.i_alpha.value)
            beta_raw = int(dut.i_beta.value)
            return alpha_raw, beta_raw

    raise TimeoutError("Clarke did not complete")


# -----------------------------------------------------------------------
# Test: Spec test vectors (math.md section 6.1)
# -----------------------------------------------------------------------

@cocotb.test()
async def test_spec_vector_1(dut):
    """Test 1: Ia=+1.0, Ib=-0.5 -> Ialpha=1.0, Ibeta=0.0"""
    await init_clarke(dut)

    ia = foc_to_fixed(1.0)    # 0x7FFF (closest to +1.0)
    ib = foc_to_fixed(-0.5)   # 0xC000

    alpha_raw, beta_raw = await run_clarke(dut, ia, ib)

    # Model reference
    model_alpha, model_beta = clarke_fixed(ia, ib, CFG)

    LOG.info(f"Test 1: alpha={foc_from_fixed(foc_to_signed(alpha_raw)):.6f}, "
             f"beta={foc_from_fixed(foc_to_signed(beta_raw)):.6f}")

    # Bit-exact match with Python model
    assert_close_ulp(alpha_raw, foc_to_unsigned(model_alpha), ulps=0, width=DATA_W,
                     msg="alpha vs model")
    assert_close_ulp(beta_raw, foc_to_unsigned(model_beta), ulps=0, width=DATA_W,
                     msg="beta vs model")

    # Approximate check vs spec
    assert_close_ulp(alpha_raw, foc_to_unsigned(foc_to_fixed(1.0)), ulps=1, width=DATA_W,
                     msg="alpha ~ 1.0")
    assert_close_ulp(beta_raw, foc_to_unsigned(foc_to_fixed(0.0)), ulps=2, width=DATA_W,
                     msg="beta ~ 0.0")


@cocotb.test()
async def test_zero_input(dut):
    """All-zero input should produce all-zero output."""
    await init_clarke(dut)

    alpha_raw, beta_raw = await run_clarke(dut, 0, 0)

    model_alpha, model_beta = clarke_fixed(0, 0, CFG)

    LOG.info(f"Zero: alpha={foc_to_signed(alpha_raw)}, beta={foc_to_signed(beta_raw)}")

    assert_close_ulp(alpha_raw, foc_to_unsigned(model_alpha), ulps=0, width=DATA_W,
                     msg="zero alpha")
    assert_close_ulp(beta_raw, foc_to_unsigned(model_beta), ulps=0, width=DATA_W,
                     msg="zero beta")


@cocotb.test()
async def test_negative_full_scale(dut):
    """Ia=-1.0, Ib=+0.5 (opposite of test 1)."""
    await init_clarke(dut)

    ia = foc_to_fixed(-1.0)   # 0x8000
    ib = foc_to_fixed(0.5)    # 0x4000

    alpha_raw, beta_raw = await run_clarke(dut, ia, ib)
    model_alpha, model_beta = clarke_fixed(ia, ib, CFG)

    LOG.info(f"NegFS: alpha={foc_from_fixed(foc_to_signed(alpha_raw)):.6f}, "
             f"beta={foc_from_fixed(foc_to_signed(beta_raw)):.6f}")

    assert_close_ulp(alpha_raw, foc_to_unsigned(model_alpha), ulps=0, width=DATA_W,
                     msg="neg full-scale alpha")
    assert_close_ulp(beta_raw, foc_to_unsigned(model_beta), ulps=0, width=DATA_W,
                     msg="neg full-scale beta")


@cocotb.test()
async def test_in_range_intermediate(dut):
    """Inputs where (ia + 2*ib) stays within Q1.15 range."""
    await init_clarke(dut)

    test_cases = [
        (0.3, -0.15),    # ia+2*ib = 0.0
        (0.0, 0.25),     # ia+2*ib = 0.5
        (-0.25, 0.0),    # ia+2*ib = -0.25
        (0.5, -0.5),     # ia+2*ib = -0.5
        (-0.3, 0.4),     # ia+2*ib = 0.5
    ]

    for ia_f, ib_f in test_cases:
        ia = foc_to_fixed(ia_f)
        ib = foc_to_fixed(ib_f)

        alpha_raw, beta_raw = await run_clarke(dut, ia, ib)
        model_alpha, model_beta = clarke_fixed(ia, ib, CFG)

        LOG.info(f"ia={ia_f}, ib={ib_f}: "
                 f"alpha={foc_from_fixed(foc_to_signed(alpha_raw)):.6f}, "
                 f"beta={foc_from_fixed(foc_to_signed(beta_raw)):.6f}")

        assert_close_ulp(alpha_raw, foc_to_unsigned(model_alpha), ulps=0, width=DATA_W,
                         msg=f"ia={ia_f},ib={ib_f} alpha")
        assert_close_ulp(beta_raw, foc_to_unsigned(model_beta), ulps=0, width=DATA_W,
                         msg=f"ia={ia_f},ib={ib_f} beta")


@cocotb.test()
async def test_random_vectors(dut):
    """50 random input vectors, verified bit-exact against Python model."""
    await init_clarke(dut)

    random.seed(42)

    for i in range(50):
        ia_f = random.uniform(-0.5, 0.5)
        ib_f = random.uniform(-0.5, 0.5)
        ia = foc_to_fixed(ia_f)
        ib = foc_to_fixed(ib_f)

        alpha_raw, beta_raw = await run_clarke(dut, ia, ib)
        model_alpha, model_beta = clarke_fixed(ia, ib, CFG)

        assert_close_ulp(alpha_raw, foc_to_unsigned(model_alpha), ulps=0, width=DATA_W,
                         msg=f"random[{i}] alpha")
        assert_close_ulp(beta_raw, foc_to_unsigned(model_beta), ulps=0, width=DATA_W,
                         msg=f"random[{i}] beta")

    LOG.info("50 random vectors passed (bit-exact)")


@cocotb.test()
async def test_clarke_latency(dut):
    """Verify Clarke produces done after exactly 2 cycles."""
    await init_clarke(dut)

    dut.ia.value = foc_to_unsigned(foc_to_fixed(0.5))
    dut.ib.value = foc_to_unsigned(foc_to_fixed(-0.25))
    dut.en.value = 1
    await RisingEdge(dut.clk)
    dut.en.value = 0

    latency = 0
    for _ in range(20):
        await RisingEdge(dut.clk)
        latency += 1
        if dut.done.value == 1:
            break

    LOG.info(f"Measured Clarke latency: {latency} cycles (expected {CLARKE_LATENCY})")
    assert latency == CLARKE_LATENCY, \
        f"Expected {CLARKE_LATENCY} cycles, got {latency}"


@cocotb.test()
async def test_alpha_equals_ia(dut):
    """Verify i_alpha = ia for all inputs (Clarke identity)."""
    await init_clarke(dut)

    for ia_f in [-0.9, -0.5, -0.1, 0.0, 0.1, 0.5, 0.9]:
        ia = foc_to_fixed(ia_f)
        ib = foc_to_fixed(0.0)

        alpha_raw, _ = await run_clarke(dut, ia, ib)

        assert foc_to_signed(alpha_raw) == ia, \
            f"i_alpha should equal ia: got {foc_to_signed(alpha_raw)}, expected {ia}"

    LOG.info("i_alpha = ia identity verified")
