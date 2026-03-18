"""Cocotb tests for foc_pi (PI controller with anti-windup).

Tests against the SPECIFICATION (math.md section 6.3, microarch section 5).
Verifies Kp-only step, integrator ramp, anti-windup saturation,
20-step accumulation, and clear/reset.
"""

import cocotb
from cocotb.triggers import RisingEdge, ClockCycles

from foc_tb_utils import (
    start_clock, reset_dut, get_logger,
    foc_to_fixed, foc_from_fixed, foc_to_unsigned, foc_to_signed,
    assert_close_ulp,
    DATA_W, FRAC_W, PI_ACC_W, CORDIC_ITERS, PWM_BITS,
)
from foc_fixed_point import FixedPointConfig, pi_fixed, to_fixed, from_fixed

LOG = get_logger("pi")
CFG = FixedPointConfig(data_w=DATA_W, frac_w=FRAC_W, cordic_iters=CORDIC_ITERS, pwm_bits=PWM_BITS, pi_acc_w=PI_ACC_W)

# PI latency: 4 cycles (from microarch)
PI_LATENCY = 4


async def init_pi(dut):
    """Initialize PI controller: clock, reset, deassert signals."""
    start_clock(dut)
    dut.en.value = 0
    dut.clear.value = 0
    dut.ref_val.value = 0
    dut.meas_val.value = 0
    dut.kp.value = 0
    dut.ki.value = 0
    dut.out_max.value = foc_to_unsigned(CFG.max_int)
    dut.int_max.value = foc_to_unsigned(CFG.max_int)
    await reset_dut(dut)
    # Clear integrator
    dut.clear.value = 1
    await RisingEdge(dut.clk)
    dut.clear.value = 0
    await RisingEdge(dut.clk)


async def run_pi_step(dut, ref: int, meas: int, kp: int, ki: int,
                      out_max: int, int_max: int,
                      timeout: int = 20) -> int:
    """Drive one PI step, wait for done, return output value."""
    dut.ref_val.value = foc_to_unsigned(ref)
    dut.meas_val.value = foc_to_unsigned(meas)
    dut.kp.value = foc_to_unsigned(kp)
    dut.ki.value = foc_to_unsigned(ki)
    dut.out_max.value = foc_to_unsigned(out_max)
    dut.int_max.value = foc_to_unsigned(int_max)
    dut.en.value = 1
    await RisingEdge(dut.clk)
    dut.en.value = 0

    for _ in range(timeout):
        await RisingEdge(dut.clk)
        if dut.done.value == 1:
            return int(dut.out_val.value)

    raise TimeoutError("PI did not complete")


# -----------------------------------------------------------------------
# Spec test vectors (math.md section 6.3)
# -----------------------------------------------------------------------

@cocotb.test()
async def test_kp_only_step(dut):
    """Test 1: Kp=0.5, Ki=0, ref=0.8, meas=0.0 -> output=0.4"""
    await init_pi(dut)

    kp = foc_to_fixed(0.5)
    ki = 0
    ref = foc_to_fixed(0.8)
    meas = foc_to_fixed(0.0)
    out_max = CFG.max_int
    int_max = CFG.max_int

    out_raw = await run_pi_step(dut, ref, meas, kp, ki, out_max, int_max)
    out_f = foc_from_fixed(foc_to_signed(out_raw))

    # Model reference
    model_out, _ = pi_fixed(ref, meas, kp, ki, out_max, int_max, 0, CFG)

    LOG.info(f"Kp-only: output={out_f:.6f} (expected ~0.4, model={from_fixed(model_out, CFG):.6f})")

    assert_close_ulp(out_raw, foc_to_unsigned(model_out), ulps=0, width=DATA_W,
                     msg="Kp-only step")


@cocotb.test()
async def test_kp_only_negative_error(dut):
    """Kp=0.5, ref=0.0, meas=0.8 -> output=-0.4 (negative error)."""
    await init_pi(dut)

    kp = foc_to_fixed(0.5)
    ref = foc_to_fixed(0.0)
    meas = foc_to_fixed(0.8)
    out_max = CFG.max_int
    int_max = CFG.max_int

    out_raw = await run_pi_step(dut, ref, meas, kp, 0, out_max, int_max)
    model_out, _ = pi_fixed(ref, meas, kp, 0, out_max, int_max, 0, CFG)

    LOG.info(f"Neg error: output={foc_from_fixed(foc_to_signed(out_raw)):.6f}")
    assert_close_ulp(out_raw, foc_to_unsigned(model_out), ulps=0, width=DATA_W,
                     msg="Kp negative error")


@cocotb.test()
async def test_integrator_ramp(dut):
    """Test 2: Ki-only accumulation over 20 steps.
    Kp=0, Ki=0.1, ref=0.5, meas=0.0
    Each step should add ~0.05 to the output.
    """
    await init_pi(dut)

    kp = 0
    ki = foc_to_fixed(0.1)
    ref = foc_to_fixed(0.5)
    meas = foc_to_fixed(0.0)
    out_max = CFG.max_int
    int_max = CFG.max_int

    # Run model in parallel
    integrator = 0

    for step in range(20):
        out_raw = await run_pi_step(dut, ref, meas, kp, ki, out_max, int_max)
        model_out, integrator = pi_fixed(ref, meas, kp, ki, out_max, int_max,
                                          integrator, CFG)

        out_f = foc_from_fixed(foc_to_signed(out_raw))
        model_f = from_fixed(model_out, CFG)

        if step < 5 or step == 19:
            LOG.info(f"Step {step}: RTL={out_f:.6f}, model={model_f:.6f}")

        assert_close_ulp(out_raw, foc_to_unsigned(model_out), ulps=0, width=DATA_W,
                         msg=f"integrator ramp step {step}")


@cocotb.test()
async def test_anti_windup_saturation(dut):
    """Test 3: Anti-windup prevents integrator from growing past saturation.
    Kp=0.5, Ki=0.1, out_max=0.9
    After output saturates, integrator should freeze.
    """
    await init_pi(dut)

    kp = foc_to_fixed(0.5)
    ki = foc_to_fixed(0.1)
    ref = foc_to_fixed(0.999)
    meas = foc_to_fixed(0.0)
    out_max = foc_to_fixed(0.9)
    int_max = foc_to_fixed(0.9)

    integrator = 0
    prev_out = None

    for step in range(15):
        out_raw = await run_pi_step(dut, ref, meas, kp, ki, out_max, int_max)
        model_out, integrator = pi_fixed(ref, meas, kp, ki, out_max, int_max,
                                          integrator, CFG)

        out_f = foc_from_fixed(foc_to_signed(out_raw))
        LOG.info(f"Step {step}: output={out_f:.6f}")

        assert_close_ulp(out_raw, foc_to_unsigned(model_out), ulps=0, width=DATA_W,
                         msg=f"anti-windup step {step}")

        # After initial ramp-up, output should be clamped at out_max
        if step >= 5:
            assert out_f <= 0.9 + CFG.resolution, \
                f"Step {step}: output {out_f} exceeds out_max=0.9"

    LOG.info("Anti-windup saturation verified")


@cocotb.test()
async def test_clear_reset(dut):
    """Verify clear signal resets PI integrator to zero."""
    await init_pi(dut)

    kp = 0
    ki = foc_to_fixed(0.5)
    ref = foc_to_fixed(0.9)
    meas = foc_to_fixed(0.0)
    out_max = CFG.max_int
    int_max = CFG.max_int

    # Accumulate for 5 steps to build up integrator
    for _ in range(5):
        await run_pi_step(dut, ref, meas, kp, ki, out_max, int_max)

    out_raw = await run_pi_step(dut, ref, meas, kp, ki, out_max, int_max)
    out_before_clear = foc_from_fixed(foc_to_signed(out_raw))
    LOG.info(f"Before clear: output={out_before_clear:.6f}")
    assert out_before_clear != 0.0, "Output should be non-zero before clear"

    # Assert clear
    dut.clear.value = 1
    await RisingEdge(dut.clk)
    dut.clear.value = 0
    await RisingEdge(dut.clk)

    # Run one more step — integrator should be zero, so output = kp*e only
    out_raw = await run_pi_step(dut, ref, meas, kp, ki, out_max, int_max)
    out_after_clear = foc_from_fixed(foc_to_signed(out_raw))
    LOG.info(f"After clear (step 0): output={out_after_clear:.6f}")

    # With kp=0 and fresh integrator, first step should be ki*e/scale (small)
    # Not zero because ki*e delta is added in this step
    model_out, _ = pi_fixed(ref, meas, kp, ki, out_max, int_max, 0, CFG)
    assert_close_ulp(out_raw, foc_to_unsigned(model_out), ulps=0, width=DATA_W,
                     msg="after clear")

    LOG.info("Clear/reset verified")


@cocotb.test()
async def test_pi_latency(dut):
    """Verify PI produces done after expected number of cycles."""
    await init_pi(dut)

    dut.ref_val.value = foc_to_unsigned(foc_to_fixed(0.5))
    dut.meas_val.value = 0
    dut.kp.value = foc_to_unsigned(foc_to_fixed(0.5))
    dut.ki.value = 0
    dut.out_max.value = foc_to_unsigned(CFG.max_int)
    dut.int_max.value = foc_to_unsigned(CFG.max_int)
    dut.en.value = 1
    await RisingEdge(dut.clk)
    dut.en.value = 0

    latency = 0
    for _ in range(20):
        await RisingEdge(dut.clk)
        latency += 1
        if dut.done.value == 1:
            break

    LOG.info(f"Measured PI latency: {latency} cycles (expected {PI_LATENCY})")
    assert latency == PI_LATENCY, f"Expected {PI_LATENCY} cycles, got {latency}"


@cocotb.test()
async def test_symmetric_response(dut):
    """Positive and negative errors should produce symmetric outputs."""
    await init_pi(dut)

    kp = foc_to_fixed(0.5)
    out_max = CFG.max_int
    int_max = CFG.max_int

    # Positive error
    ref_pos = foc_to_fixed(0.6)
    meas_pos = foc_to_fixed(0.0)
    out_pos_raw = await run_pi_step(dut, ref_pos, meas_pos, kp, 0, out_max, int_max)
    out_pos = foc_to_signed(out_pos_raw)

    # Clear integrator
    dut.clear.value = 1
    await RisingEdge(dut.clk)
    dut.clear.value = 0
    await RisingEdge(dut.clk)

    # Negative error (same magnitude)
    ref_neg = foc_to_fixed(0.0)
    meas_neg = foc_to_fixed(0.6)
    out_neg_raw = await run_pi_step(dut, ref_neg, meas_neg, kp, 0, out_max, int_max)
    out_neg = foc_to_signed(out_neg_raw)

    LOG.info(f"Pos error output: {out_pos}, Neg error output: {out_neg}")

    # Should be negations of each other (within 1 ULP for truncation)
    assert abs(out_pos + out_neg) <= 1, \
        f"Asymmetric: pos={out_pos}, neg={out_neg}, sum={out_pos + out_neg}"
