#!/usr/bin/env python3
"""FOC Motor Coprocessor — Golden (Float64) Model.

Implements the full FOC pipeline in double-precision floating point
as the reference for RTL verification:
  1. Clarke transform (3-phase abc -> alpha-beta)
  2. Park transform (alpha-beta -> dq via rotation)
  3. PI controllers (Id loop, Iq loop)
  4. Inverse Park transform (dq -> alpha-beta)
  5. SVPWM output generation

TODO: Agent 1 (spec agent) will fill in the complete mathematical
specification. This skeleton provides the structure and a basic
sanity check.

Usage:
    python foc_golden.py          # Quick sanity check
    python foc_golden.py --full   # Full verification suite
"""

import argparse
import math
import sys


# ---------------------------------------------------------------------------
# Clarke Transform
# ---------------------------------------------------------------------------

def clarke(ia: float, ib: float, ic: float) -> tuple[float, float]:
    """Clarke transform: 3-phase (abc) -> 2-phase (alpha-beta).

    Amplitude-invariant form:
        i_alpha = i_a
        i_beta  = (i_a + 2*i_b) / sqrt(3)

    Assumes balanced: ia + ib + ic = 0
    """
    # TODO: Agent 1 will confirm variant (amplitude vs power invariant)
    i_alpha = ia
    i_beta = (ia + 2.0 * ib) / math.sqrt(3.0)
    return i_alpha, i_beta


# ---------------------------------------------------------------------------
# Park Transform
# ---------------------------------------------------------------------------

def park(i_alpha: float, i_beta: float, theta: float) -> tuple[float, float]:
    """Park transform: stationary frame -> rotating frame.

    i_d =  i_alpha * cos(theta) + i_beta * sin(theta)
    i_q = -i_alpha * sin(theta) + i_beta * cos(theta)
    """
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    i_d = i_alpha * cos_t + i_beta * sin_t
    i_q = -i_alpha * sin_t + i_beta * cos_t
    return i_d, i_q


# ---------------------------------------------------------------------------
# PI Controller
# ---------------------------------------------------------------------------

class PIController:
    """Discrete PI controller with anti-windup."""

    def __init__(self, kp: float, ki: float, limit: float):
        self.kp = kp
        self.ki = ki
        self.limit = limit
        self.integrator = 0.0

    def step(self, error: float) -> float:
        """Compute one PI step. Returns control output."""
        self.integrator += self.ki * error
        # Anti-windup clamp
        self.integrator = max(-self.limit, min(self.limit, self.integrator))
        output = self.kp * error + self.integrator
        return max(-self.limit, min(self.limit, output))

    def reset(self):
        self.integrator = 0.0


# ---------------------------------------------------------------------------
# Inverse Park Transform
# ---------------------------------------------------------------------------

def inverse_park(v_d: float, v_q: float, theta: float) -> tuple[float, float]:
    """Inverse Park: rotating frame -> stationary frame.

    v_alpha = v_d * cos(theta) - v_q * sin(theta)
    v_beta  = v_d * sin(theta) + v_q * cos(theta)
    """
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    v_alpha = v_d * cos_t - v_q * sin_t
    v_beta = v_d * sin_t + v_q * cos_t
    return v_alpha, v_beta


# ---------------------------------------------------------------------------
# SVPWM
# ---------------------------------------------------------------------------

def svpwm(v_alpha: float, v_beta: float, v_dc: float) -> tuple[float, float, float]:
    """Space Vector PWM: alpha-beta voltages -> 3-phase duty cycles.

    TODO: Agent 1 will specify the full SVPWM algorithm
    (sector identification, switching times, duty cycle calculation).

    Returns duty cycles (da, db, dc) in range [0, 1].
    """
    # Placeholder: simple inverse Clarke for now
    # Real SVPWM uses sector-based switching patterns
    va = v_alpha
    vb = (-v_alpha + math.sqrt(3.0) * v_beta) / 2.0
    vc = (-v_alpha - math.sqrt(3.0) * v_beta) / 2.0

    # Normalize to duty cycles [0, 1]
    v_max = max(va, vb, vc)
    v_min = min(va, vb, vc)
    offset = -(v_max + v_min) / 2.0  # Center-aligned (SVPWM min-max injection)

    da = (va + offset) / v_dc + 0.5
    db = (vb + offset) / v_dc + 0.5
    dc = (vc + offset) / v_dc + 0.5

    return (
        max(0.0, min(1.0, da)),
        max(0.0, min(1.0, db)),
        max(0.0, min(1.0, dc)),
    )


# ---------------------------------------------------------------------------
# Full FOC Pipeline
# ---------------------------------------------------------------------------

def foc_step(
    ia: float, ib: float, ic: float,
    theta: float,
    id_ref: float, iq_ref: float,
    pi_d: PIController, pi_q: PIController,
    v_dc: float = 1.0,
) -> tuple[float, float, float]:
    """Execute one FOC control iteration.

    Args:
        ia, ib, ic: Phase currents (measured).
        theta: Rotor electrical angle (radians).
        id_ref, iq_ref: Reference currents in dq frame.
        pi_d, pi_q: PI controller instances for d and q axes.
        v_dc: DC bus voltage.

    Returns:
        (da, db, dc): Duty cycles for three phases.
    """
    # 1. Clarke
    i_alpha, i_beta = clarke(ia, ib, ic)

    # 2. Park
    i_d, i_q = park(i_alpha, i_beta, theta)

    # 3. PI control
    v_d = pi_d.step(id_ref - i_d)
    v_q = pi_q.step(iq_ref - i_q)

    # 4. Inverse Park
    v_alpha, v_beta = inverse_park(v_d, v_q, theta)

    # 5. SVPWM
    da, db, dc = svpwm(v_alpha, v_beta, v_dc)

    return da, db, dc


# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------

def sanity_check():
    """Basic sanity checks for each FOC stage."""
    print("FOC Golden Model — Sanity Check")
    print("=" * 40)

    # Clarke: balanced 3-phase at 0 degrees
    # ia=1, ib=-0.5, ic=-0.5 (balanced, phase a at peak)
    ia, ib, ic = 1.0, -0.5, -0.5
    alpha, beta = clarke(ia, ib, ic)
    print(f"Clarke({ia}, {ib}, {ic}) -> alpha={alpha:.4f}, beta={beta:.4f}")
    assert abs(alpha - 1.0) < 1e-10, "Clarke alpha failed"
    assert abs(beta - 0.0) < 1e-10, f"Clarke beta failed: {beta}"
    print("  PASS")

    # Park: alpha=1, beta=0, theta=0 -> d=1, q=0
    d, q = park(1.0, 0.0, 0.0)
    print(f"Park(1, 0, theta=0) -> d={d:.4f}, q={q:.4f}")
    assert abs(d - 1.0) < 1e-10
    assert abs(q - 0.0) < 1e-10
    print("  PASS")

    # Park: alpha=1, beta=0, theta=pi/2 -> d=0, q=-1
    # (q is negative because: q = -alpha*sin + beta*cos = -1*1 + 0*0 = -1)
    d, q = park(1.0, 0.0, math.pi / 2)
    print(f"Park(1, 0, theta=pi/2) -> d={d:.4f}, q={q:.4f}")
    assert abs(d - 0.0) < 1e-10
    assert abs(q - (-1.0)) < 1e-10
    print("  PASS")

    # Inverse Park roundtrip
    v_alpha, v_beta = inverse_park(0.5, 0.3, 0.7)
    d2, q2 = park(v_alpha, v_beta, 0.7)
    print(f"InvPark roundtrip: d={d2:.4f} (expected 0.5), q={q2:.4f} (expected 0.3)")
    assert abs(d2 - 0.5) < 1e-10
    assert abs(q2 - 0.3) < 1e-10
    print("  PASS")

    # PI controller basic step
    pi = PIController(kp=1.0, ki=0.1, limit=10.0)
    out = pi.step(1.0)
    print(f"PI(error=1.0) -> {out:.4f}")
    assert abs(out - 1.1) < 1e-10  # kp*1 + ki*1
    print("  PASS")

    # Full pipeline smoke test
    pi_d = PIController(kp=0.5, ki=0.01, limit=1.0)
    pi_q = PIController(kp=0.5, ki=0.01, limit=1.0)
    da, db, dc = foc_step(1.0, -0.5, -0.5, 0.0, 0.0, 0.5, pi_d, pi_q)
    print(f"FOC step -> duties: ({da:.4f}, {db:.4f}, {dc:.4f})")
    assert 0.0 <= da <= 1.0 and 0.0 <= db <= 1.0 and 0.0 <= dc <= 1.0
    print("  PASS")

    print("")
    print("All sanity checks passed.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="Run full verification suite")
    args = parser.parse_args()

    sanity_check()

    if args.full:
        print("\nTODO: Full verification suite (sweep angles, multi-step convergence)")


if __name__ == "__main__":
    main()
