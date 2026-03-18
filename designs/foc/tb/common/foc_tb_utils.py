"""FOC-specific testbench utilities for cocotb tests.

Extends the shared infra/tb_utils.py with FOC-domain helpers.
"""

import sys
import os
import math

# Add infra and model directories to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'infra'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'model'))

from tb_utils import (  # noqa: E402
    to_fixed, from_fixed, to_signed, to_unsigned,
    assert_close, assert_close_ulp, start_clock, reset_dut, get_logger,
)

# FOC parameters — read from environment (set by Makefile) or use defaults
DATA_W = int(os.environ.get("DATA_W", 16))
FRAC_W = int(os.environ.get("FRAC_W", 15))
PWM_BITS = int(os.environ.get("PWM_BITS", 10))
ANGLE_W = int(os.environ.get("ANGLE_W", 16))
PI_ACC_W = int(os.environ.get("PI_ACC_W", 2 * DATA_W))
CORDIC_ITERS = int(os.environ.get("CORDIC_ITERS", 16))
PIPE_DEPTH = int(os.environ.get("PIPE_DEPTH", 1))
SHARED_MUL = int(os.environ.get("SHARED_MUL", 0))

# Derived constants
SCALE = 1 << FRAC_W
MAX_INT = (1 << (DATA_W - 1)) - 1
MIN_INT = -(1 << (DATA_W - 1))
PWM_MAX = (1 << PWM_BITS) - 1
HALF_SCALE = 1 << (PWM_BITS - 1)

# Fixed-point constants scaled to current FRAC_W
INV_K = round(0.607253 * (1 << FRAC_W))    # 1/K CORDIC gain
INV_SQRT3 = round(0.577350 * (1 << FRAC_W))  # 1/sqrt(3)

# Legacy aliases
INV_K_Q15 = INV_K
INV_SQRT3_Q15 = INV_SQRT3


# ---------------------------------------------------------------------------
# Fixed-point conversion with FOC defaults
# ---------------------------------------------------------------------------

def foc_to_fixed(value: float) -> int:
    """Convert float to FOC fixed-point (Q1.15) using default parameters."""
    return to_fixed(value, FRAC_W, DATA_W)


def foc_from_fixed(value: int) -> float:
    """Convert FOC fixed-point (Q1.15) to float using default parameters."""
    return from_fixed(value, FRAC_W, DATA_W)


def foc_to_unsigned(signed_val: int) -> int:
    """Convert signed Python int to unsigned DATA_W representation for RTL."""
    return to_unsigned(signed_val, DATA_W)


def foc_to_signed(unsigned_val: int) -> int:
    """Convert unsigned DATA_W RTL value to signed Python int."""
    return to_signed(unsigned_val, DATA_W)


# ---------------------------------------------------------------------------
# Angle helpers
# ---------------------------------------------------------------------------

def angle_to_uint16(degrees: float) -> int:
    """Convert degrees to unsigned 16-bit angle representation.

    0x0000 = 0 deg, 0x4000 = 90 deg, 0x8000 = 180 deg, etc.
    """
    return round(degrees / 360.0 * 65536) & 0xFFFF


def radians_to_angle(radians: float) -> int:
    """Convert radians to unsigned ANGLE_W representation."""
    return round(radians / (2.0 * math.pi) * (1 << ANGLE_W)) & ((1 << ANGLE_W) - 1)


def angle_to_degrees(angle_uint: int) -> float:
    """Convert unsigned angle to degrees."""
    return (angle_uint & 0xFFFF) / 65536.0 * 360.0


# ---------------------------------------------------------------------------
# FOC-domain reference functions (floating-point, for quick checks)
# ---------------------------------------------------------------------------

def clarke_reference(ia: float, ib: float) -> tuple[float, float]:
    """Clarke transform (float reference)."""
    i_alpha = ia
    i_beta = (ia + 2.0 * ib) / math.sqrt(3.0)
    return i_alpha, i_beta


def park_reference(i_alpha: float, i_beta: float, theta_rad: float) -> tuple[float, float]:
    """Park transform (float reference)."""
    cos_t = math.cos(theta_rad)
    sin_t = math.sin(theta_rad)
    i_d = i_alpha * cos_t + i_beta * sin_t
    i_q = -i_alpha * sin_t + i_beta * cos_t
    return i_d, i_q


def inv_park_reference(v_d: float, v_q: float, theta_rad: float) -> tuple[float, float]:
    """Inverse Park transform (float reference)."""
    cos_t = math.cos(theta_rad)
    sin_t = math.sin(theta_rad)
    v_alpha = v_d * cos_t - v_q * sin_t
    v_beta = v_d * sin_t + v_q * cos_t
    return v_alpha, v_beta


# ---------------------------------------------------------------------------
# Wishbone helpers for foc_top tests
# ---------------------------------------------------------------------------

# Register map offsets
REG_CTRL     = 0x00
REG_STATUS   = 0x04
REG_IA       = 0x08
REG_IB       = 0x0C
REG_THETA    = 0x10
REG_ID_REF   = 0x14
REG_IQ_REF   = 0x18
REG_KP       = 0x1C
REG_KI       = 0x20
REG_OUT_MAX  = 0x24
REG_INT_MAX  = 0x28
REG_KP_Q     = 0x2C
REG_KI_Q     = 0x30
REG_DUTY_A   = 0x40
REG_DUTY_B   = 0x44
REG_DUTY_C   = 0x48
REG_ID_MEAS  = 0x50
REG_IQ_MEAS  = 0x54
REG_IALPHA   = 0x58
REG_IBETA    = 0x5C
REG_VD       = 0x60
REG_VQ       = 0x64
REG_VALPHA   = 0x68
REG_VBETA    = 0x6C
REG_PI_D_INT = 0x70
REG_PI_Q_INT = 0x74
REG_PARAM0   = 0x80
REG_PARAM1   = 0x84

# CTRL register bits
CTRL_START      = 1 << 0
CTRL_CLEAR      = 1 << 1
CTRL_CONTINUOUS = 1 << 2
CTRL_PI_RESET   = 1 << 3

# STATUS register bits
STATUS_BUSY  = 1 << 0
STATUS_DONE  = 1 << 1
STATUS_ERROR = 1 << 2


async def wb_write(dut, addr: int, data: int):
    """Wishbone single write cycle."""
    from cocotb.triggers import RisingEdge

    dut.wb_cyc_i.value = 1
    dut.wb_stb_i.value = 1
    dut.wb_we_i.value = 1
    dut.wb_adr_i.value = addr
    dut.wb_dat_i.value = data & 0xFFFFFFFF
    await RisingEdge(dut.clk)

    # Wait for ack
    for _ in range(10):
        if dut.wb_ack_o.value == 1:
            break
        await RisingEdge(dut.clk)

    dut.wb_cyc_i.value = 0
    dut.wb_stb_i.value = 0
    dut.wb_we_i.value = 0
    await RisingEdge(dut.clk)


async def wb_read(dut, addr: int) -> int:
    """Wishbone single read cycle. Returns 32-bit data."""
    from cocotb.triggers import RisingEdge

    dut.wb_cyc_i.value = 1
    dut.wb_stb_i.value = 1
    dut.wb_we_i.value = 0
    dut.wb_adr_i.value = addr
    await RisingEdge(dut.clk)

    for _ in range(10):
        if dut.wb_ack_o.value == 1:
            break
        await RisingEdge(dut.clk)

    data = int(dut.wb_dat_o.value)
    dut.wb_cyc_i.value = 0
    dut.wb_stb_i.value = 0
    await RisingEdge(dut.clk)
    return data


async def wb_wait_done(dut, timeout: int = 500):
    """Poll STATUS register until done bit is set."""
    from cocotb.triggers import RisingEdge

    for _ in range(timeout):
        status = await wb_read(dut, REG_STATUS)
        if status & STATUS_DONE:
            return status
        await RisingEdge(dut.clk)
    raise TimeoutError("FOC computation did not complete within timeout")
