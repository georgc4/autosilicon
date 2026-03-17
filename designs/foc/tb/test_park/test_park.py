"""Cocotb tests for foc_park (Park transform: alpha-beta -> dq).

Tests against the SPECIFICATION (math.md section 6.2).
The Park module receives sin/cos values directly (precomputed by CORDIC).
Verifies spec test vectors, roundtrip with inverse Park, and random inputs.
"""

import cocotb
from cocotb.triggers import RisingEdge, ClockCycles
import math
import random

from foc_tb_utils import (
    start_clock, reset_dut, get_logger,
    foc_to_fixed, foc_from_fixed, foc_to_unsigned, foc_to_signed,
    assert_close_ulp, angle_to_uint16,
    DATA_W, FRAC_W,
)
from foc_fixed_point import (
    FixedPointConfig, park_fixed, inv_park_fixed, sincos_fixed, to_fixed,
)

LOG = get_logger("park")
CFG = FixedPointConfig()

# Park latency: 2-6 cycles depending on config (spec: 2 for dedicated mul)
PARK_MAX_LATENCY = 10


async def init_park(dut):
    """Initialize Park module."""
    start_clock(dut)
    dut.en.value = 0
    dut.i_alpha.value = 0
    dut.i_beta.value = 0
    dut.sin_val.value = 0
    dut.cos_val.value = 0
    await reset_dut(dut)


async def run_park(dut, i_alpha: int, i_beta: int, sin_v: int, cos_v: int,
                   timeout: int = PARK_MAX_LATENCY) -> tuple[int, int]:
    """Drive inputs, pulse en, wait for done, return (i_d, i_q)."""
    dut.i_alpha.value = foc_to_unsigned(i_alpha)
    dut.i_beta.value = foc_to_unsigned(i_beta)
    dut.sin_val.value = foc_to_unsigned(sin_v)
    dut.cos_val.value = foc_to_unsigned(cos_v)
    dut.en.value = 1
    await RisingEdge(dut.clk)
    dut.en.value = 0

    for _ in range(timeout):
        await RisingEdge(dut.clk)
        if dut.done.value == 1:
            id_raw = int(dut.i_d.value)
            iq_raw = int(dut.i_q.value)
            return id_raw, iq_raw

    raise TimeoutError("Park did not complete")


# -----------------------------------------------------------------------
# Spec test vectors (math.md section 6.2)
# -----------------------------------------------------------------------

@cocotb.test()
async def test_theta_0_identity(dut):
    """Test 1: theta=0 (identity rotation).
    Ialpha=0.5, Ibeta=0.866 -> Id=0.5, Iq=0.866
    """
    await init_park(dut)

    cos_v, sin_v = sincos_fixed(0x0000, CFG)  # theta=0: cos=1, sin=0
    ia = foc_to_fixed(0.5)
    ib = foc_to_fixed(0.866)

    id_raw, iq_raw = await run_park(dut, ia, ib, sin_v, cos_v)
    model_id, model_iq = park_fixed(ia, ib, sin_v, cos_v, CFG)

    id_f = foc_from_fixed(foc_to_signed(id_raw))
    iq_f = foc_from_fixed(foc_to_signed(iq_raw))
    LOG.info(f"theta=0: Id={id_f:.6f} (exp ~0.5), Iq={iq_f:.6f} (exp ~0.866)")

    assert_close_ulp(id_raw, foc_to_unsigned(model_id), ulps=0, width=DATA_W,
                     msg="theta=0 Id")
    assert_close_ulp(iq_raw, foc_to_unsigned(model_iq), ulps=0, width=DATA_W,
                     msg="theta=0 Iq")


@cocotb.test()
async def test_theta_90(dut):
    """Test 2: theta=90.
    Ialpha=1.0, Ibeta=0.0 -> Id~0, Iq~-1.0
    """
    await init_park(dut)

    cos_v, sin_v = sincos_fixed(0x4000, CFG)
    ia = foc_to_fixed(1.0)
    ib = foc_to_fixed(0.0)

    id_raw, iq_raw = await run_park(dut, ia, ib, sin_v, cos_v)
    model_id, model_iq = park_fixed(ia, ib, sin_v, cos_v, CFG)

    LOG.info(f"theta=90: Id={foc_from_fixed(foc_to_signed(id_raw)):.6f}, "
             f"Iq={foc_from_fixed(foc_to_signed(iq_raw)):.6f}")

    assert_close_ulp(id_raw, foc_to_unsigned(model_id), ulps=0, width=DATA_W,
                     msg="theta=90 Id")
    assert_close_ulp(iq_raw, foc_to_unsigned(model_iq), ulps=0, width=DATA_W,
                     msg="theta=90 Iq")


@cocotb.test()
async def test_theta_30(dut):
    """Test 3: theta=30.
    Ialpha=0.75, Ibeta=0.25 -> Id~0.7745, Iq~-0.1585
    """
    await init_park(dut)

    cos_v, sin_v = sincos_fixed(0x1555, CFG)
    ia = foc_to_fixed(0.75)
    ib = foc_to_fixed(0.25)

    id_raw, iq_raw = await run_park(dut, ia, ib, sin_v, cos_v)
    model_id, model_iq = park_fixed(ia, ib, sin_v, cos_v, CFG)

    LOG.info(f"theta=30: Id={foc_from_fixed(foc_to_signed(id_raw)):.6f} (exp ~0.7745), "
             f"Iq={foc_from_fixed(foc_to_signed(iq_raw)):.6f} (exp ~-0.1585)")

    assert_close_ulp(id_raw, foc_to_unsigned(model_id), ulps=0, width=DATA_W,
                     msg="theta=30 Id")
    assert_close_ulp(iq_raw, foc_to_unsigned(model_iq), ulps=0, width=DATA_W,
                     msg="theta=30 Iq")


@cocotb.test()
async def test_park_inv_park_roundtrip(dut):
    """Park followed by inverse Park should return ~original values.

    Note: This test drives foc_park only. The roundtrip is computed in Python
    model to verify that park_fixed -> inv_park_fixed is identity (within
    truncation error), then we verify RTL matches model.
    """
    await init_park(dut)

    angles_deg = [0, 30, 45, 60, 90, 120, 180, 270]

    for deg in angles_deg:
        theta = angle_to_uint16(deg)
        cos_v, sin_v = sincos_fixed(theta, CFG)

        ia = foc_to_fixed(0.6)
        ib = foc_to_fixed(0.3)

        # Forward Park (RTL)
        id_raw, iq_raw = await run_park(dut, ia, ib, sin_v, cos_v)
        id_s = foc_to_signed(id_raw)
        iq_s = foc_to_signed(iq_raw)

        # Forward Park (model)
        model_id, model_iq = park_fixed(ia, ib, sin_v, cos_v, CFG)
        assert id_s == model_id, f"RTL/model mismatch at {deg}deg Id"
        assert iq_s == model_iq, f"RTL/model mismatch at {deg}deg Iq"

        # Roundtrip via model inverse Park
        rt_alpha, rt_beta = inv_park_fixed(model_id, model_iq, sin_v, cos_v, CFG)

        # Check roundtrip error (expect small due to truncation)
        err_a = abs(rt_alpha - ia)
        err_b = abs(rt_beta - ib)
        LOG.info(f"{deg}deg roundtrip: err_alpha={err_a} ULP, err_beta={err_b} ULP")
        assert err_a <= 5, f"Roundtrip alpha error too large at {deg}deg: {err_a}"
        assert err_b <= 5, f"Roundtrip beta error too large at {deg}deg: {err_b}"


@cocotb.test()
async def test_random_park(dut):
    """30 random input vectors, bit-exact match with Python model."""
    await init_park(dut)

    random.seed(123)

    for i in range(30):
        ia_f = random.uniform(-0.9, 0.9)
        ib_f = random.uniform(-0.9, 0.9)
        deg = random.uniform(0, 360)

        ia = foc_to_fixed(ia_f)
        ib = foc_to_fixed(ib_f)
        theta = angle_to_uint16(deg)
        cos_v, sin_v = sincos_fixed(theta, CFG)

        id_raw, iq_raw = await run_park(dut, ia, ib, sin_v, cos_v)
        model_id, model_iq = park_fixed(ia, ib, sin_v, cos_v, CFG)

        assert_close_ulp(id_raw, foc_to_unsigned(model_id), ulps=0, width=DATA_W,
                         msg=f"random[{i}] Id")
        assert_close_ulp(iq_raw, foc_to_unsigned(model_iq), ulps=0, width=DATA_W,
                         msg=f"random[{i}] Iq")

    LOG.info("30 random Park vectors passed (bit-exact)")
