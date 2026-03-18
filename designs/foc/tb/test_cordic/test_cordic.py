"""Cocotb tests for foc_cordic (CORDIC sin/cos generator).

Tests against the SPECIFICATION (math.md), not RTL implementation details.
Verifies known angles, all 4 quadrants, and a full 360-degree sweep.
"""

import cocotb
from cocotb.triggers import RisingEdge, ClockCycles
import math

from foc_tb_utils import (
    start_clock, reset_dut, get_logger,
    foc_to_fixed, foc_from_fixed, foc_to_unsigned, foc_to_signed,
    assert_close_ulp,
    angle_to_uint16, angle_to_degrees,
    DATA_W, FRAC_W, ANGLE_W, CORDIC_ITERS,
)
from foc_fixed_point import FixedPointConfig, sincos_fixed

LOG = get_logger("cordic")
CFG = FixedPointConfig(data_w=DATA_W, frac_w=FRAC_W, cordic_iters=CORDIC_ITERS, angle_w=ANGLE_W)

# Scale ULP tolerance: fewer CORDIC iterations = less precision
# At CI=16/DW=16 we see ~3 ULPs; at CI=8/DW=16 expect ~256 ULPs
CORDIC_ULP_TOLERANCE = max(4, 1 << max(0, DATA_W - CORDIC_ITERS + 1))

# CORDIC latency: CORDIC_ITERS + 2 (pre-rotate + iterations + post-correct)
CORDIC_LATENCY = CORDIC_ITERS + 2


async def init_cordic(dut):
    """Initialize CORDIC: start clock, reset, deassert start."""
    start_clock(dut)
    dut.start.value = 0
    dut.theta.value = 0
    await reset_dut(dut)


async def run_cordic(dut, theta_uint: int, timeout: int = 50) -> tuple[int, int]:
    """Drive theta, pulse start, wait for done, return (cos_val, sin_val)."""
    dut.theta.value = theta_uint & 0xFFFF
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0

    for _ in range(timeout):
        await RisingEdge(dut.clk)
        if dut.done.value == 1:
            cos_raw = int(dut.cos_val.value)
            sin_raw = int(dut.sin_val.value)
            return cos_raw, sin_raw

    raise TimeoutError(f"CORDIC did not complete for theta=0x{theta_uint:04X}")


# -----------------------------------------------------------------------
# Test: Known angles from math.md section 6.4
# -----------------------------------------------------------------------

@cocotb.test()
async def test_known_angles(dut):
    """Verify CORDIC output for known angles from spec (0, 45, 90, 150, 210, 270, 315 deg)."""
    await init_cordic(dut)

    test_cases = [
        # (description, theta_uint16, expected_cos, expected_sin)
        ("0 deg",   0x0000, 1.0,             0.0),
        ("45 deg",  0x2000, math.sqrt(2)/2,  math.sqrt(2)/2),
        ("90 deg",  0x4000, 0.0,             1.0),
        ("150 deg", 0x6AAA, -math.sqrt(3)/2, 0.5),
        ("210 deg", 0x9555, -math.sqrt(3)/2, -0.5),
        ("270 deg", 0xC000, 0.0,             -1.0),
        ("315 deg", 0xE000, math.sqrt(2)/2,  -math.sqrt(2)/2),
    ]

    for desc, theta, exp_cos, exp_sin in test_cases:
        cos_raw, sin_raw = await run_cordic(dut, theta)

        # Get Python model expected values for bit-exact comparison
        model_cos, model_sin = sincos_fixed(theta, CFG)

        cos_signed = foc_to_signed(cos_raw)
        sin_signed = foc_to_signed(sin_raw)
        cos_f = foc_from_fixed(cos_signed)
        sin_f = foc_from_fixed(sin_signed)

        LOG.info(f"{desc}: cos={cos_f:.6f} (exp {exp_cos:.6f}), "
                 f"sin={sin_f:.6f} (exp {exp_sin:.6f})")

        # Verify against Python model (bit-exact)
        assert_close_ulp(cos_raw, foc_to_unsigned(model_cos), ulps=0, width=DATA_W,
                         msg=f"{desc} cos vs model")
        assert_close_ulp(sin_raw, foc_to_unsigned(model_sin), ulps=0, width=DATA_W,
                         msg=f"{desc} sin vs model")

        # Verify against math reference (within 3 ULP)
        exp_cos_fp = foc_to_fixed(exp_cos)
        exp_sin_fp = foc_to_fixed(exp_sin)
        assert_close_ulp(cos_raw, foc_to_unsigned(exp_cos_fp), ulps=CORDIC_ULP_TOLERANCE, width=DATA_W,
                         msg=f"{desc} cos vs math")
        assert_close_ulp(sin_raw, foc_to_unsigned(exp_sin_fp), ulps=CORDIC_ULP_TOLERANCE, width=DATA_W,
                         msg=f"{desc} sin vs math")


@cocotb.test()
async def test_all_quadrants(dut):
    """Verify correct signs in all 4 quadrants."""
    await init_cordic(dut)

    # One angle per quadrant (not on boundary)
    quadrant_tests = [
        ("Q1 (30 deg)",  angle_to_uint16(30),  True,  True),   # cos>0, sin>0
        ("Q2 (120 deg)", angle_to_uint16(120), False, True),    # cos<0, sin>0
        ("Q3 (210 deg)", angle_to_uint16(210), False, False),   # cos<0, sin<0
        ("Q4 (300 deg)", angle_to_uint16(300), True,  False),   # cos>0, sin<0
    ]

    for desc, theta, cos_pos, sin_pos in quadrant_tests:
        cos_raw, sin_raw = await run_cordic(dut, theta)
        cos_s = foc_to_signed(cos_raw)
        sin_s = foc_to_signed(sin_raw)

        LOG.info(f"{desc}: cos={foc_from_fixed(cos_s):.6f}, sin={foc_from_fixed(sin_s):.6f}")

        if cos_pos:
            assert cos_s > 0, f"{desc}: cos should be positive, got {cos_s}"
        else:
            assert cos_s < 0, f"{desc}: cos should be negative, got {cos_s}"

        if sin_pos:
            assert sin_s > 0, f"{desc}: sin should be positive, got {sin_s}"
        else:
            assert sin_s < 0, f"{desc}: sin should be negative, got {sin_s}"


@cocotb.test()
async def test_full_sweep(dut):
    """Sweep 360 degrees in 1-degree steps, verify RTL matches Python model.

    The model match (0 ULP) is the correctness gate — this MUST pass.
    Math accuracy vs IEEE cos/sin is a quality metric, logged but not asserted,
    because the error is inherent to the CORDIC algorithm at a given DATA_W
    and shrinks with wider word widths.
    """
    await init_cordic(dut)

    max_cos_err = 0
    max_sin_err = 0

    for deg in range(360):
        theta = angle_to_uint16(deg)
        cos_raw, sin_raw = await run_cordic(dut, theta)

        # Reference from Python model (should be bit-exact with RTL)
        model_cos, model_sin = sincos_fixed(theta, CFG)

        assert_close_ulp(cos_raw, foc_to_unsigned(model_cos), ulps=0, width=DATA_W,
                         msg=f"sweep {deg}deg cos vs model")
        assert_close_ulp(sin_raw, foc_to_unsigned(model_sin), ulps=0, width=DATA_W,
                         msg=f"sweep {deg}deg sin vs model")

        # Track max error vs math reference (metric only, not a gate)
        cos_err = abs(foc_to_signed(cos_raw) - foc_to_fixed(math.cos(math.radians(deg))))
        sin_err = abs(foc_to_signed(sin_raw) - foc_to_fixed(math.sin(math.radians(deg))))
        max_cos_err = max(max_cos_err, cos_err)
        max_sin_err = max(max_sin_err, sin_err)

    max_ulp = max(max_cos_err, max_sin_err)
    LOG.info(f"CORDIC_ACCURACY max_cos_ulp={max_cos_err} max_sin_ulp={max_sin_err} max_ulp={max_ulp}")


@cocotb.test()
async def test_accuracy_sweep(dut):
    """Sweep 360 degrees and report math accuracy as a metric.

    This test always passes — it exists only to record the max ULP error
    vs IEEE math.cos/sin into the test log for metric extraction.
    The accuracy is algorithm-inherent at a given DATA_W.
    """
    await init_cordic(dut)

    max_cos_err = 0
    max_sin_err = 0
    worst_angle = 0

    for deg in range(360):
        theta = angle_to_uint16(deg)
        cos_raw, sin_raw = await run_cordic(dut, theta)

        cos_err = abs(foc_to_signed(cos_raw) - foc_to_fixed(math.cos(math.radians(deg))))
        sin_err = abs(foc_to_signed(sin_raw) - foc_to_fixed(math.sin(math.radians(deg))))
        err = max(cos_err, sin_err)
        if err > max(max_cos_err, max_sin_err):
            worst_angle = deg
        max_cos_err = max(max_cos_err, cos_err)
        max_sin_err = max(max_sin_err, sin_err)

    max_ulp = max(max_cos_err, max_sin_err)
    LOG.info(f"CORDIC_ACCURACY max_cos_ulp={max_cos_err} max_sin_ulp={max_sin_err} "
             f"max_ulp={max_ulp} worst_angle={worst_angle}deg")


@cocotb.test()
async def test_cordic_latency(dut):
    """Verify CORDIC produces done after the expected number of cycles."""
    await init_cordic(dut)

    dut.theta.value = 0x2000  # 45 degrees
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0

    latency = 0
    for _ in range(100):
        await RisingEdge(dut.clk)
        latency += 1
        if dut.done.value == 1:
            break

    LOG.info(f"Measured CORDIC latency: {latency} cycles (expected {CORDIC_LATENCY})")
    assert latency == CORDIC_LATENCY, \
        f"Expected {CORDIC_LATENCY} cycles, got {latency}"


@cocotb.test()
async def test_sin_cos_identity(dut):
    """Verify sin^2 + cos^2 ≈ 1 for various angles."""
    await init_cordic(dut)

    for deg in [0, 30, 45, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330]:
        theta = angle_to_uint16(deg)
        cos_raw, sin_raw = await run_cordic(dut, theta)

        cos_f = foc_from_fixed(foc_to_signed(cos_raw))
        sin_f = foc_from_fixed(foc_to_signed(sin_raw))
        magnitude_sq = cos_f * cos_f + sin_f * sin_f

        LOG.info(f"{deg}deg: sin^2+cos^2 = {magnitude_sq:.6f}")
        assert abs(magnitude_sq - 1.0) < 0.001, \
            f"Identity check failed at {deg}deg: sin^2+cos^2={magnitude_sq}"


@cocotb.test()
async def test_back_to_back(dut):
    """Verify two consecutive CORDIC computations produce correct results."""
    await init_cordic(dut)

    # First computation: 30 degrees
    cos1, sin1 = await run_cordic(dut, angle_to_uint16(30))
    # Second computation: 210 degrees (should be negation)
    cos2, sin2 = await run_cordic(dut, angle_to_uint16(210))

    cos1_s = foc_to_signed(cos1)
    sin1_s = foc_to_signed(sin1)
    cos2_s = foc_to_signed(cos2)
    sin2_s = foc_to_signed(sin2)

    LOG.info(f"30deg:  cos={foc_from_fixed(cos1_s):.6f}, sin={foc_from_fixed(sin1_s):.6f}")
    LOG.info(f"210deg: cos={foc_from_fixed(cos2_s):.6f}, sin={foc_from_fixed(sin2_s):.6f}")

    # cos(210) = -cos(30), sin(210) = -sin(30)
    assert abs(cos1_s + cos2_s) <= 1, \
        f"cos(30)+cos(210) should be ~0, got {cos1_s + cos2_s}"
    assert abs(sin1_s + sin2_s) <= 1, \
        f"sin(30)+sin(210) should be ~0, got {sin1_s + sin2_s}"
