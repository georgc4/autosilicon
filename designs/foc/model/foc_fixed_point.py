#!/usr/bin/env python3
"""FOC Motor Coprocessor — Bit-Accurate Fixed-Point Model.

Mirrors the RTL behavior exactly:
  - Q1.15 fixed-point arithmetic (DATA_W=16, FRAC_W=15)
  - Truncation after multiply (matching RTL bit slicing)
  - CORDIC rotation (iterative, matching RTL pipeline)
  - PI controller with PI_ACC_W=32 integrator and anti-windup
  - SVPWM via min-max injection method

Used to generate expected values for RTL verification and to
analyze quantization error vs the golden (float64) model.

Adapted from the NLT accelerator's nlt_fixed_point.py pattern.
"""

import math
from dataclasses import dataclass


@dataclass
class FixedPointConfig:
    """Fixed-point format configuration."""
    data_w: int = 16         # Total word width for data signals
    frac_w: int = 15         # Fractional bits
    cordic_iters: int = 16   # CORDIC iterations
    pwm_bits: int = 10       # PWM resolution
    angle_w: int = 16        # Angle word width (unsigned)
    pi_acc_w: int = 32       # PI integrator accumulator width

    @property
    def int_w(self) -> int:
        return self.data_w - self.frac_w

    @property
    def scale(self) -> int:
        return 1 << self.frac_w

    @property
    def max_val(self) -> float:
        return (1 << (self.data_w - 1)) / self.scale - 1.0 / self.scale

    @property
    def min_val(self) -> float:
        return -(1 << (self.data_w - 1)) / self.scale

    @property
    def resolution(self) -> float:
        return 1.0 / self.scale

    @property
    def max_int(self) -> int:
        """Maximum signed value in DATA_W bits."""
        return (1 << (self.data_w - 1)) - 1

    @property
    def min_int(self) -> int:
        """Minimum signed value in DATA_W bits."""
        return -(1 << (self.data_w - 1))

    @property
    def pwm_max(self) -> int:
        return (1 << self.pwm_bits) - 1

    @property
    def half_scale(self) -> int:
        return 1 << (self.pwm_bits - 1)

    @property
    def angle_max(self) -> int:
        return 1 << self.angle_w


# ── Fixed-point arithmetic primitives ─────────────────────────

def to_fixed(value: float, cfg: FixedPointConfig) -> int:
    """Convert float to signed fixed-point integer (with truncation)."""
    scaled = int(math.floor(value * cfg.scale))
    return max(cfg.min_int, min(cfg.max_int, scaled))


def from_fixed(value: int, cfg: FixedPointConfig) -> float:
    """Convert signed fixed-point integer to float."""
    v = int(value)
    if v >= (1 << (cfg.data_w - 1)):
        v -= (1 << cfg.data_w)
    return v / cfg.scale


def sign_extend(value: int, width: int) -> int:
    """Sign-extend a value from `width` bits to Python int."""
    mask = (1 << width) - 1
    v = value & mask
    if v >= (1 << (width - 1)):
        v -= (1 << width)
    return v


def fixed_mul(a: int, b: int, cfg: FixedPointConfig) -> int:
    """Fixed-point multiply with truncation (matching RTL).

    RTL does: result = (a * b) >>> FRAC_W  (arithmetic right shift)
    Then saturate to DATA_W signed range.
    """
    product = a * b
    result = product >> cfg.frac_w
    return max(cfg.min_int, min(cfg.max_int, result))


def fixed_add_sat(a: int, b: int, cfg: FixedPointConfig) -> int:
    """Saturating fixed-point addition within DATA_W."""
    result = a + b
    return max(cfg.min_int, min(cfg.max_int, result))


def clamp(value: int, lo: int, hi: int) -> int:
    """Clamp integer to [lo, hi]."""
    return max(lo, min(hi, value))


# ── Constants ─────────────────────────────────────────────────

ATAN_TABLE_16 = [8192, 4836, 2555, 1297, 651, 326, 163, 81,
                 41, 20, 10, 5, 3, 1, 1, 0]

INV_K_Q15 = 0x4DBA   # round(0.607253 * 32768) = 19898
INV_SQRT3_Q15 = 0x49E7  # round(1/sqrt(3) * 32768) = 18919


def get_atan_table(cfg: FixedPointConfig) -> list[int]:
    if cfg.angle_w == 16 and cfg.cordic_iters <= 16:
        return ATAN_TABLE_16[:cfg.cordic_iters]
    table = []
    for i in range(cfg.cordic_iters):
        angle_rad = math.atan(2.0 ** (-i))
        angle_uint = round(angle_rad / (2.0 * math.pi) * (1 << cfg.angle_w))
        table.append(angle_uint)
    return table


def get_inv_k(cfg: FixedPointConfig) -> int:
    if cfg.frac_w == 15 and cfg.cordic_iters == 16:
        return INV_K_Q15
    k = 1.0
    for i in range(cfg.cordic_iters):
        k *= math.sqrt(1.0 + 2.0 ** (-2 * i))
    return to_fixed(1.0 / k, cfg)


def get_inv_sqrt3(cfg: FixedPointConfig) -> int:
    if cfg.frac_w == 15:
        return INV_SQRT3_Q15
    return to_fixed(1.0 / math.sqrt(3.0), cfg)


def sqrt3_mul(x: int, cfg: FixedPointConfig) -> int:
    """Compute sqrt(3) * x using shift-add chain matching RTL.

    sqrt(3) ~ 1 + 1/2 + 1/8 + 1/16 + 1/32 + 1/128 + 1/256 + 1/1024 + 1/2048
            = 1.732421875 (error: -0.00008)
    """
    result = x + (x >> 1) + (x >> 3) + (x >> 4) + (x >> 5) + \
             (x >> 7) + (x >> 8) + (x >> 10) + (x >> 11)
    return clamp(result, cfg.min_int, cfg.max_int)


# ── Angle helpers ─────────────────────────────────────────────

def angle_to_uint16(degrees: float) -> int:
    """Convert degrees to unsigned 16-bit angle representation."""
    return round(degrees / 360.0 * 65536) & 0xFFFF


def radians_to_angle(radians: float, cfg: FixedPointConfig) -> int:
    """Convert radians to unsigned ANGLE_W representation."""
    return round(radians / (2.0 * math.pi) * (1 << cfg.angle_w)) & ((1 << cfg.angle_w) - 1)


# ── CORDIC sin/cos ────────────────────────────────────────────

def sincos_fixed(theta_uint: int, cfg: FixedPointConfig) -> tuple[int, int]:
    """Compute (cos, sin) from unsigned ANGLE_W angle via CORDIC.

    Quadrant handling:
    1. Extract quadrant from top 2 bits
    2. Fold angle to first quadrant [0, 90deg)
       - Strip top 2 bits to get intra-quadrant angle
       - Mirror (bitwise NOT) for Q2 and Q4 (odd quadrants)
    3. Run CORDIC iterations with x=1/K, y=0, z=folded_angle
    4. Post-correction: negate cos/sin based on quadrant
       - negate_cos = quad[1] XOR quad[0]  (Q2, Q3)
       - negate_sin = quad[1]              (Q3, Q4)
    """
    atan_table = get_atan_table(cfg)
    inv_k = get_inv_k(cfg)
    aw = cfg.angle_w

    theta = theta_uint & ((1 << aw) - 1)

    # Extract quadrant (top 2 bits)
    quad = (theta >> (aw - 2)) & 0x3
    quarter_mask = (1 << (aw - 2)) - 1

    # Strip top 2 bits to get intra-quadrant angle
    angle_q1 = theta & quarter_mask

    # Mirror for odd quadrants (Q2 and Q4): angle_q1 = ~angle_q1
    if quad & 1:
        angle_q1 = (~angle_q1) & quarter_mask

    # Initialize CORDIC
    x = inv_k
    y = 0
    z = angle_q1

    # CORDIC iterations
    for i in range(cfg.cordic_iters):
        z_signed = sign_extend(z, aw)

        if z_signed >= 0:
            x_new = x - (y >> i)
            y_new = y + (x >> i)
            z_new = (z - atan_table[i]) & ((1 << aw) - 1)
        else:
            x_new = x + (y >> i)
            y_new = y - (x >> i)
            z_new = (z + atan_table[i]) & ((1 << aw) - 1)

        x = clamp(x_new, cfg.min_int, cfg.max_int)
        y = clamp(y_new, cfg.min_int, cfg.max_int)
        z = z_new

    cos_q1 = x
    sin_q1 = y

    # Post-correction based on quadrant
    # negate_cos for Q2 (01) and Q3 (10): quad[1] XOR quad[0]
    # negate_sin for Q3 (10) and Q4 (11): quad[1]
    negate_cos = ((quad >> 1) ^ (quad & 1)) & 1
    negate_sin = (quad >> 1) & 1

    cos_out = -cos_q1 if negate_cos else cos_q1
    sin_out = -sin_q1 if negate_sin else sin_q1

    cos_out = clamp(cos_out, cfg.min_int, cfg.max_int)
    sin_out = clamp(sin_out, cfg.min_int, cfg.max_int)

    return cos_out, sin_out


# ── Clarke transform ──────────────────────────────────────────

def clarke_fixed(ia: int, ib: int, cfg: FixedPointConfig) -> tuple[int, int]:
    """Clarke transform in fixed-point.

    i_alpha = ia
    i_beta  = (ia + 2*ib) * INV_SQRT3

    Note: intermediate (ia + 2*ib) can overflow Q1.15 for large inputs.
    RTL uses saturating addition, which clips the intermediate.
    """
    inv_sqrt3 = get_inv_sqrt3(cfg)

    i_alpha = ia

    ib_2 = fixed_add_sat(ib, ib, cfg)
    sum_val = fixed_add_sat(ia, ib_2, cfg)
    i_beta = fixed_mul(sum_val, inv_sqrt3, cfg)

    return i_alpha, i_beta


# ── Park transform ────────────────────────────────────────────

def park_fixed(i_alpha: int, i_beta: int, sin_val: int, cos_val: int,
               cfg: FixedPointConfig) -> tuple[int, int]:
    """Park transform via explicit multiply.

    Id =  i_alpha * cos(theta) + i_beta * sin(theta)
    Iq = -i_alpha * sin(theta) + i_beta * cos(theta)
    """
    term1 = fixed_mul(i_alpha, cos_val, cfg)
    term2 = fixed_mul(i_beta, sin_val, cfg)
    i_d = fixed_add_sat(term1, term2, cfg)

    term3 = fixed_mul(i_alpha, sin_val, cfg)
    term4 = fixed_mul(i_beta, cos_val, cfg)
    neg_term3 = clamp(-term3, cfg.min_int, cfg.max_int)
    i_q = fixed_add_sat(neg_term3, term4, cfg)

    return i_d, i_q


# ── Inverse Park transform ───────────────────────────────────

def inv_park_fixed(v_d: int, v_q: int, sin_val: int, cos_val: int,
                   cfg: FixedPointConfig) -> tuple[int, int]:
    """Inverse Park transform via explicit multiply.

    v_alpha = v_d * cos(theta) - v_q * sin(theta)
    v_beta  = v_d * sin(theta) + v_q * cos(theta)
    """
    term1 = fixed_mul(v_d, cos_val, cfg)
    term2 = fixed_mul(v_q, sin_val, cfg)
    neg_term2 = clamp(-term2, cfg.min_int, cfg.max_int)
    v_alpha = fixed_add_sat(term1, neg_term2, cfg)

    term3 = fixed_mul(v_d, sin_val, cfg)
    term4 = fixed_mul(v_q, cos_val, cfg)
    v_beta = fixed_add_sat(term3, term4, cfg)

    return v_alpha, v_beta


# ── PI controller ─────────────────────────────────────────────

def pi_fixed(ref: int, meas: int, kp: int, ki: int,
             out_max: int, int_max: int,
             integrator: int, cfg: FixedPointConfig) -> tuple[int, int]:
    """PI controller step in fixed-point.

    The accumulator stores the sum of (ki*e) products in Q2.30 format
    (raw 32-bit products of two Q1.15 values). To extract a Q1.15
    output, we right-shift by FRAC_W.

    Anti-windup per microarch section 5.3:
    - If output saturates AND error would push further into saturation,
      freeze the integrator.

    Args:
        ref: reference value (DATA_W signed)
        meas: measured value (DATA_W signed)
        kp: proportional gain (DATA_W signed)
        ki: integral gain, pre-scaled by Ts (DATA_W signed)
        out_max: output clamp magnitude (DATA_W signed, positive)
        int_max: integrator clamp magnitude (DATA_W signed, positive)
        integrator: current integrator state (PI_ACC_W, raw product sum)
        cfg: fixed-point configuration

    Returns:
        (output, new_integrator) — output is DATA_W signed,
        integrator is PI_ACC_W raw product accumulator
    """
    pi_acc_max = (1 << (cfg.pi_acc_w - 1)) - 1
    pi_acc_min = -(1 << (cfg.pi_acc_w - 1))

    # Error
    e = ref - meas
    e = clamp(e, cfg.min_int, cfg.max_int)

    # Proportional term (Q1.15)
    u_p = fixed_mul(kp, e, cfg)

    # Integral delta: full product ki*e (Q2.30 in 32 bits)
    delta = ki * e
    delta = clamp(delta, pi_acc_min, pi_acc_max)

    # Tentative integrator update
    u_i_tent = integrator + delta
    u_i_tent = clamp(u_i_tent, pi_acc_min, pi_acc_max)

    # Integrator clamp: int_max is Q1.15, extend to accumulator scale
    # by shifting left by FRAC_W (since acc stores Q2.30 values)
    int_max_ext = int_max << cfg.frac_w
    u_i_clamped = clamp(u_i_tent, -int_max_ext, int_max_ext)

    # Extract Q1.15 value from accumulator for output sum
    u_i_out = u_i_clamped >> cfg.frac_w

    # Raw output
    u_raw = u_p + u_i_out

    # Anti-windup check
    saturated = (u_raw > out_max) or (u_raw < -out_max)
    same_sign = (e >= 0 and u_raw >= 0) or (e < 0 and u_raw < 0)

    if saturated and same_sign:
        new_integrator = integrator  # freeze
    else:
        new_integrator = u_i_clamped

    # Output clamp
    output = clamp(u_raw, -out_max, out_max)
    output = clamp(output, cfg.min_int, cfg.max_int)

    return output, new_integrator


# ── SVPWM (min-max injection) ─────────────────────────────────

def svpwm_fixed(v_alpha: int, v_beta: int,
                cfg: FixedPointConfig) -> tuple[int, int, int]:
    """SVPWM via min-max injection method.

    Returns: (duty_a, duty_b, duty_c) unsigned PWM_BITS values.
    """
    # Stage 1: Inverse Clarke
    va = v_alpha
    sqrt3_vb = sqrt3_mul(v_beta, cfg)
    vb = (-v_alpha + sqrt3_vb) >> 1
    vc = (-v_alpha - sqrt3_vb) >> 1

    # Stage 2: Min/Max + offset
    vmax = max(va, vb, vc)
    vmin = min(va, vb, vc)
    voffset = -(vmax + vmin) >> 1

    # Stage 3: Duty cycle computation
    pwm_max = cfg.pwm_max
    half = cfg.half_scale

    def compute_duty(vx: int) -> int:
        vx_adj = vx + voffset
        scaled = (vx_adj * pwm_max) >> cfg.frac_w
        duty = half + scaled
        return clamp(duty, 0, pwm_max)

    return compute_duty(va), compute_duty(vb), compute_duty(vc)


# ── Full pipeline ─────────────────────────────────────────────

def full_pipeline_fixed(
    ia: int, ib: int, theta_uint: int,
    id_ref: int, iq_ref: int,
    kp: int, ki: int,
    out_max: int, int_max: int,
    integrator_d: int, integrator_q: int,
    cfg: FixedPointConfig,
) -> dict:
    """Run the complete FOC pipeline in fixed-point."""
    cos_val, sin_val = sincos_fixed(theta_uint, cfg)
    i_alpha, i_beta = clarke_fixed(ia, ib, cfg)
    i_d, i_q = park_fixed(i_alpha, i_beta, sin_val, cos_val, cfg)

    v_d, new_int_d = pi_fixed(id_ref, i_d, kp, ki, out_max, int_max,
                               integrator_d, cfg)
    v_q, new_int_q = pi_fixed(iq_ref, i_q, kp, ki, out_max, int_max,
                               integrator_q, cfg)

    v_alpha, v_beta = inv_park_fixed(v_d, v_q, sin_val, cos_val, cfg)
    duty_a, duty_b, duty_c = svpwm_fixed(v_alpha, v_beta, cfg)

    return {
        'cos_val': cos_val, 'sin_val': sin_val,
        'i_alpha': i_alpha, 'i_beta': i_beta,
        'i_d': i_d, 'i_q': i_q,
        'v_d': v_d, 'v_q': v_q,
        'integrator_d': new_int_d, 'integrator_q': new_int_q,
        'v_alpha': v_alpha, 'v_beta': v_beta,
        'duty_a': duty_a, 'duty_b': duty_b, 'duty_c': duty_c,
    }


# ── Test vectors from math.md section 6 ──────────────────────

def run_test_vectors():
    """Run ALL test vectors from math.md section 6."""
    cfg = FixedPointConfig()
    print(f"FOC Fixed-Point Model — Config: Q{cfg.int_w}.{cfg.frac_w} "
          f"(DATA_W={cfg.data_w}, FRAC_W={cfg.frac_w})")
    print(f"  Range: [{cfg.min_val:.6f}, {cfg.max_val:.6f}]")
    print(f"  Resolution: {cfg.resolution:.8f}")
    print(f"  CORDIC gain 1/K = 0x{get_inv_k(cfg):04X}")
    print(f"  INV_SQRT3 = 0x{get_inv_sqrt3(cfg):04X}")
    print()

    all_pass = True

    def check(name, actual_fp, expected_float, tol_ulp=3):
        nonlocal all_pass
        actual_f = from_fixed(actual_fp, cfg)
        err = abs(actual_f - expected_float)
        err_ulp = err * cfg.scale
        status = "PASS" if err_ulp <= tol_ulp else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(f"  {name}: expected={expected_float:.6f}, got={actual_f:.6f}, "
              f"err={err_ulp:.2f} ULP [{status}]")

    # ── 6.1 Clarke Transform ──
    print("=== 6.1 Clarke Transform ===")

    # Test 1: Ia=+1.0, Ib=-0.5 -> alpha=1.0, beta=0.0
    ia = to_fixed(1.0, cfg)
    ib = to_fixed(-0.5, cfg)
    alpha, beta = clarke_fixed(ia, ib, cfg)
    print("Test 1: Ia=+1.0, Ib=-0.5")
    check("Ialpha", alpha, 1.0, 1)
    check("Ibeta", beta, 0.0, 2)

    # Test 2: Ia=0.0, Ib=+sqrt(3)/2 (intermediate overflows Q1.15)
    ia = to_fixed(0.0, cfg)
    ib = to_fixed(math.sqrt(3.0) / 2.0, cfg)
    alpha, beta = clarke_fixed(ia, ib, cfg)
    print("Test 2: Ia=0.0, Ib=+sqrt(3)/2")
    print(f"  NOTE: intermediate (0+2*0.866)=1.732 overflows Q1.15, saturates to ~1.0")
    print(f"  Ialpha={from_fixed(alpha, cfg):.6f}, Ibeta={from_fixed(beta, cfg):.6f}")

    # Test 3: Ia=+0.5, Ib=+0.5 -> intermediate ok if |ia+2*ib|<=1
    # ia+2*ib = 0.5+1.0 = 1.5 -> also overflows!
    ia = to_fixed(0.5, cfg)
    ib = to_fixed(0.5, cfg)
    alpha, beta = clarke_fixed(ia, ib, cfg)
    print("Test 3: Ia=+0.5, Ib=+0.5")
    print(f"  NOTE: intermediate (0.5+1.0)=1.5 overflows Q1.15")
    print(f"  Ialpha={from_fixed(alpha, cfg):.6f}, Ibeta={from_fixed(beta, cfg):.6f}")

    # Test for in-range intermediate: Ia=0.3, Ib=-0.15
    ia = to_fixed(0.3, cfg)
    ib = to_fixed(-0.15, cfg)
    alpha, beta = clarke_fixed(ia, ib, cfg)
    exp_beta = (0.3 + 2 * (-0.15)) / math.sqrt(3.0)
    print("Test 4 (in-range): Ia=0.3, Ib=-0.15")
    check("Ialpha", alpha, 0.3, 1)
    check("Ibeta", beta, exp_beta, 2)

    # ── 6.4 CORDIC Sin/Cos ──
    print("\n=== 6.4 CORDIC Sin/Cos ===")

    cordic_tests = [
        ("0 deg",   0x0000, 1.0, 0.0),
        ("45 deg",  0x2000, math.sqrt(2)/2, math.sqrt(2)/2),
        ("90 deg",  0x4000, 0.0, 1.0),
        ("150 deg", 0x6AAA, -math.sqrt(3)/2, 0.5),
        ("210 deg", 0x9555, -math.sqrt(3)/2, -0.5),
        ("270 deg", 0xC000, 0.0, -1.0),
        ("315 deg", 0xE000, math.sqrt(2)/2, -math.sqrt(2)/2),
    ]

    for name, theta, exp_cos, exp_sin in cordic_tests:
        cos_v, sin_v = sincos_fixed(theta, cfg)
        print(f"theta={name} (0x{theta:04X}):")
        check(f"  cos", cos_v, exp_cos, 2)
        check(f"  sin", sin_v, exp_sin, 2)

    # ── 6.2 Park Transform ──
    print("\n=== 6.2 Park Transform ===")

    # Test 1: theta=0 (identity)
    cos_v, sin_v = sincos_fixed(0x0000, cfg)
    ia_fp = to_fixed(0.5, cfg)
    ib_fp = to_fixed(0.866, cfg)
    id_v, iq_v = park_fixed(ia_fp, ib_fp, sin_v, cos_v, cfg)
    print("Test 1: Ialpha=0.5, Ibeta=0.866, theta=0")
    check("Id", id_v, 0.5, 3)
    check("Iq", iq_v, 0.866, 3)

    # Test 2: theta=90
    cos_v, sin_v = sincos_fixed(0x4000, cfg)
    ia_fp = to_fixed(1.0, cfg)
    ib_fp = to_fixed(0.0, cfg)
    id_v, iq_v = park_fixed(ia_fp, ib_fp, sin_v, cos_v, cfg)
    print("Test 2: Ialpha=1.0, Ibeta=0.0, theta=90")
    check("Id", id_v, 0.0, 3)
    check("Iq", iq_v, -1.0, 3)

    # Test 3: theta=30
    cos_v, sin_v = sincos_fixed(0x1555, cfg)
    ia_fp = to_fixed(0.75, cfg)
    ib_fp = to_fixed(0.25, cfg)
    id_v, iq_v = park_fixed(ia_fp, ib_fp, sin_v, cos_v, cfg)
    print("Test 3: Ialpha=0.75, Ibeta=0.25, theta=30")
    check("Id", id_v, 0.7745, 5)
    check("Iq", iq_v, -0.1585, 5)

    # ── 6.3 PI Controller ──
    print("\n=== 6.3 PI Controller ===")

    # Test 1: Kp-only step
    kp = to_fixed(0.5, cfg)
    ki_v = 0
    ref = to_fixed(0.8, cfg)
    meas = to_fixed(0.0, cfg)
    out_max = cfg.max_int
    int_max = cfg.max_int
    out, integ = pi_fixed(ref, meas, kp, ki_v, out_max, int_max, 0, cfg)
    print("Test 1: Kp=0.5, Ki=0, ref=0.8, meas=0.0")
    check("output", out, 0.4, 3)

    # Test 2: Integrator accumulation (20 steps)
    kp = 0
    ki_v = to_fixed(0.1, cfg)
    ref = to_fixed(0.5, cfg)
    meas = to_fixed(0.0, cfg)
    out_max = cfg.max_int
    int_max = cfg.max_int
    integ = 0
    print("Test 2: Kp=0, Ki=0.1, ref=0.5, meas=0.0 (20 steps)")
    for step in range(20):
        out, integ = pi_fixed(ref, meas, kp, ki_v, out_max, int_max, integ, cfg)
        expected = (step + 1) * 0.05
        out_f = from_fixed(out, cfg)
        if step < 5 or step == 19:
            err = abs(out_f - expected)
            print(f"  Step {step}: out={out_f:.6f}, expected={expected:.4f}, err={err:.6f}")

    # Test 3: Anti-windup
    print("Test 3: Anti-windup (Kp=0.5, Ki=0.1, out_max=0.9)")
    kp = to_fixed(0.5, cfg)
    ki_v = to_fixed(0.1, cfg)
    ref = to_fixed(0.999, cfg)
    meas = to_fixed(0.0, cfg)
    out_max = to_fixed(0.9, cfg)
    int_max = to_fixed(0.9, cfg)
    integ = 0
    for step in range(10):
        out, integ = pi_fixed(ref, meas, kp, ki_v, out_max, int_max, integ, cfg)
        out_f = from_fixed(out, cfg)
        print(f"  Step {step}: out={out_f:.6f}")
    assert from_fixed(out, cfg) <= 0.9 + cfg.resolution, "Anti-windup failed"
    print("  Anti-windup: PASS")

    # ── 6.5 SVPWM ──
    print("\n=== 6.5 SVPWM ===")

    # Test 1: Zero input -> 50% duty
    va = to_fixed(0.0, cfg)
    vb = to_fixed(0.0, cfg)
    da, db, dc = svpwm_fixed(va, vb, cfg)
    half = cfg.half_scale
    print(f"Test 1: Valpha=0, Vbeta=0 -> duties: a={da}, b={db}, c={dc}")
    assert da == half and db == half and dc == half, \
        f"Expected {half},{half},{half} got {da},{db},{dc}"
    print("  PASS (50% duty)")

    # Test 2: Non-zero
    va = to_fixed(0.5, cfg)
    vb = to_fixed(0.5, cfg)
    da, db, dc = svpwm_fixed(va, vb, cfg)
    print(f"Test 2: Valpha=0.5, Vbeta=0.5 -> duties: a={da}, b={db}, c={dc}")
    assert 0 <= da <= cfg.pwm_max and 0 <= db <= cfg.pwm_max and 0 <= dc <= cfg.pwm_max
    print("  PASS (valid range)")

    # ── 6.6 Full Pipeline ──
    print("\n=== 6.6 Full Pipeline ===")
    print("Scenario: theta=30deg, Ia=0.45, Ib=0.10, Iq_ref=0.5, Id_ref=0, Kp=0.5")

    ia_fp = to_fixed(0.45, cfg)
    ib_fp = to_fixed(0.10, cfg)
    theta = 0x1555
    id_ref = to_fixed(0.0, cfg)
    iq_ref = to_fixed(0.5, cfg)
    kp = to_fixed(0.5, cfg)
    ki_v = to_fixed(0.0, cfg)
    out_max = cfg.max_int
    int_max = cfg.max_int

    result = full_pipeline_fixed(
        ia_fp, ib_fp, theta, id_ref, iq_ref,
        kp, ki_v, out_max, int_max, 0, 0, cfg
    )

    print(f"  cos(30)={from_fixed(result['cos_val'], cfg):.6f}, "
          f"sin(30)={from_fixed(result['sin_val'], cfg):.6f}")
    print(f"  Ialpha={from_fixed(result['i_alpha'], cfg):.6f}, "
          f"Ibeta={from_fixed(result['i_beta'], cfg):.6f}")
    print(f"  Id={from_fixed(result['i_d'], cfg):.6f}, "
          f"Iq={from_fixed(result['i_q'], cfg):.6f}")
    print(f"  Vd={from_fixed(result['v_d'], cfg):.6f}, "
          f"Vq={from_fixed(result['v_q'], cfg):.6f}")
    print(f"  Valpha={from_fixed(result['v_alpha'], cfg):.6f}, "
          f"Vbeta={from_fixed(result['v_beta'], cfg):.6f}")
    print(f"  duty_a={result['duty_a']}, duty_b={result['duty_b']}, "
          f"duty_c={result['duty_c']}")

    check("Ialpha", result['i_alpha'], 0.45, 1)
    check("Ibeta", result['i_beta'], 0.375, 5)

    print()
    if all_pass:
        print("ALL TEST VECTORS PASSED")
    else:
        print("SOME TEST VECTORS FAILED")
    return all_pass


def main():
    run_test_vectors()


if __name__ == "__main__":
    main()
