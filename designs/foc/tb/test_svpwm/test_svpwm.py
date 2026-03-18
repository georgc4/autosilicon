"""Cocotb tests for foc_svpwm (Space Vector PWM via min-max injection).

Tests against the SPECIFICATION (math.md section 6.5, microarch section 6).
Verifies sector coverage (sweep around circle), zero input (50% duty),
duty range [0, PWM_MAX], and bit-exact match with Python model.
"""

import cocotb
from cocotb.triggers import RisingEdge, ClockCycles
import math

from foc_tb_utils import (
    start_clock, reset_dut, get_logger,
    foc_to_fixed, foc_from_fixed, foc_to_unsigned, foc_to_signed,
    assert_close_ulp,
    DATA_W, FRAC_W, PWM_BITS, PWM_MAX, HALF_SCALE, CORDIC_ITERS, PI_ACC_W,
)
from foc_fixed_point import FixedPointConfig, svpwm_fixed, to_fixed

LOG = get_logger("svpwm")
CFG = FixedPointConfig(data_w=DATA_W, frac_w=FRAC_W, cordic_iters=CORDIC_ITERS, pwm_bits=PWM_BITS, pi_acc_w=PI_ACC_W)

# SVPWM latency: 4-8 cycles (from microarch)
SVPWM_MAX_LATENCY = 15


async def init_svpwm(dut):
    """Initialize SVPWM module."""
    start_clock(dut)
    dut.en.value = 0
    dut.v_alpha.value = 0
    dut.v_beta.value = 0
    await reset_dut(dut)


async def run_svpwm(dut, v_alpha: int, v_beta: int,
                    timeout: int = SVPWM_MAX_LATENCY) -> tuple[int, int, int]:
    """Drive v_alpha/v_beta, pulse en, wait done, return (duty_a, duty_b, duty_c)."""
    dut.v_alpha.value = foc_to_unsigned(v_alpha)
    dut.v_beta.value = foc_to_unsigned(v_beta)
    dut.en.value = 1
    await RisingEdge(dut.clk)
    dut.en.value = 0

    for _ in range(timeout):
        await RisingEdge(dut.clk)
        if dut.done.value == 1:
            da = int(dut.duty_a.value)
            db = int(dut.duty_b.value)
            dc = int(dut.duty_c.value)
            return da, db, dc

    raise TimeoutError("SVPWM did not complete")


# -----------------------------------------------------------------------
# Spec test vectors (math.md section 6.5)
# -----------------------------------------------------------------------

@cocotb.test()
async def test_zero_input(dut):
    """Test 2 from spec: Valpha=0, Vbeta=0 -> 50% duty on all phases."""
    await init_svpwm(dut)

    da, db, dc = await run_svpwm(dut, 0, 0)

    LOG.info(f"Zero input: da={da}, db={db}, dc={dc} (expected {HALF_SCALE})")

    assert da == HALF_SCALE, f"duty_a={da}, expected {HALF_SCALE}"
    assert db == HALF_SCALE, f"duty_b={db}, expected {HALF_SCALE}"
    assert dc == HALF_SCALE, f"duty_c={dc}, expected {HALF_SCALE}"


@cocotb.test()
async def test_spec_vectors(dut):
    """Verify spec test vectors (sections 6.5 test 1 and 3)."""
    await init_svpwm(dut)

    test_cases = [
        # (description, v_alpha_f, v_beta_f)
        ("Valpha=0.5, Vbeta=0.0", 0.5, 0.0),
        ("Valpha=0.5, Vbeta=0.5", 0.5, 0.5),
        ("Valpha=-0.3, Vbeta=0.4", -0.3, 0.4),
    ]

    for desc, va_f, vb_f in test_cases:
        va = foc_to_fixed(va_f)
        vb = foc_to_fixed(vb_f)

        da, db, dc = await run_svpwm(dut, va, vb)
        model_da, model_db, model_dc = svpwm_fixed(va, vb, CFG)

        LOG.info(f"{desc}: da={da}, db={db}, dc={dc} "
                 f"(model: {model_da}, {model_db}, {model_dc})")

        assert da == model_da, f"{desc} duty_a: RTL={da}, model={model_da}"
        assert db == model_db, f"{desc} duty_b: RTL={db}, model={model_db}"
        assert dc == model_dc, f"{desc} duty_c: RTL={dc}, model={model_dc}"


@cocotb.test()
async def test_sector_sweep(dut):
    """Sweep voltage vector around full circle (all 6 SVPWM sectors).

    Uses 36 angles at 10-degree steps with fixed magnitude.
    Verifies duty cycle range and bit-exact match with model.
    """
    await init_svpwm(dut)

    magnitude = 0.4  # Moderate amplitude (stays within linear range)

    for deg in range(0, 360, 10):
        theta = math.radians(deg)
        va_f = magnitude * math.cos(theta)
        vb_f = magnitude * math.sin(theta)

        va = foc_to_fixed(va_f)
        vb = foc_to_fixed(vb_f)

        da, db, dc = await run_svpwm(dut, va, vb)
        model_da, model_db, model_dc = svpwm_fixed(va, vb, CFG)

        # Verify range
        assert 0 <= da <= PWM_MAX, f"{deg}deg: duty_a={da} out of range"
        assert 0 <= db <= PWM_MAX, f"{deg}deg: duty_b={db} out of range"
        assert 0 <= dc <= PWM_MAX, f"{deg}deg: duty_c={dc} out of range"

        # Verify bit-exact with model
        assert da == model_da, f"{deg}deg duty_a: RTL={da}, model={model_da}"
        assert db == model_db, f"{deg}deg duty_b: RTL={db}, model={model_db}"
        assert dc == model_dc, f"{deg}deg duty_c: RTL={dc}, model={model_dc}"

    LOG.info("Full 360-degree sector sweep passed (36 angles)")


@cocotb.test()
async def test_duty_range_bounds(dut):
    """Verify duty cycles stay within [0, PWM_MAX] even for large inputs."""
    await init_svpwm(dut)

    # Large inputs that could cause overflow
    extreme_cases = [
        (0.999, 0.0),
        (-0.999, 0.0),
        (0.0, 0.999),
        (0.0, -0.999),
        (0.999, 0.999),
        (-0.999, -0.999),
    ]

    for va_f, vb_f in extreme_cases:
        va = foc_to_fixed(va_f)
        vb = foc_to_fixed(vb_f)

        da, db, dc = await run_svpwm(dut, va, vb)

        LOG.info(f"va={va_f}, vb={vb_f}: da={da}, db={db}, dc={dc}")

        assert 0 <= da <= PWM_MAX, f"duty_a={da} out of [0,{PWM_MAX}]"
        assert 0 <= db <= PWM_MAX, f"duty_b={db} out of [0,{PWM_MAX}]"
        assert 0 <= dc <= PWM_MAX, f"duty_c={dc} out of [0,{PWM_MAX}]"

    LOG.info("Duty range bounds verified for extreme inputs")


@cocotb.test()
async def test_symmetry(dut):
    """Negating both v_alpha and v_beta should produce complementary duties.

    For SVPWM with min-max injection, negating the input inverts the
    duty cycles around 50% (each duty becomes PWM_MAX - original_duty).
    """
    await init_svpwm(dut)

    va_f, vb_f = 0.3, 0.2
    va = foc_to_fixed(va_f)
    vb = foc_to_fixed(vb_f)

    da_pos, db_pos, dc_pos = await run_svpwm(dut, va, vb)

    va_neg = foc_to_fixed(-va_f)
    vb_neg = foc_to_fixed(-vb_f)
    da_neg, db_neg, dc_neg = await run_svpwm(dut, va_neg, vb_neg)

    LOG.info(f"Positive: da={da_pos}, db={db_pos}, dc={dc_pos}")
    LOG.info(f"Negative: da={da_neg}, db={db_neg}, dc={dc_neg}")

    # Complementary: duty_pos + duty_neg ≈ PWM_MAX (within a few counts)
    for phase, pos, neg in [("A", da_pos, da_neg), ("B", db_pos, db_neg), ("C", dc_pos, dc_neg)]:
        total = pos + neg
        # Allow small deviation due to truncation asymmetry
        assert abs(total - PWM_MAX) <= 3, \
            f"Phase {phase}: {pos}+{neg}={total}, expected ~{PWM_MAX}"


@cocotb.test()
async def test_back_to_back(dut):
    """Verify two consecutive SVPWM computations produce correct results."""
    await init_svpwm(dut)

    # First
    da1, db1, dc1 = await run_svpwm(dut, foc_to_fixed(0.3), foc_to_fixed(0.1))
    model1 = svpwm_fixed(foc_to_fixed(0.3), foc_to_fixed(0.1), CFG)

    # Second (different inputs)
    da2, db2, dc2 = await run_svpwm(dut, foc_to_fixed(-0.2), foc_to_fixed(0.5))
    model2 = svpwm_fixed(foc_to_fixed(-0.2), foc_to_fixed(0.5), CFG)

    assert (da1, db1, dc1) == model1, "First computation mismatch"
    assert (da2, db2, dc2) == model2, "Second computation mismatch"

    LOG.info("Back-to-back computations verified")
