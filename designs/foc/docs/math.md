# Mathematical Specification

## 1. Field-Oriented Control Algorithm

Field-Oriented Control (FOC) decouples the torque and flux components of stator current in a BLDC/PMSM motor by transforming three-phase currents into a rotating reference frame aligned with the rotor flux. In this frame, current regulation reduces to two independent DC control problems (d-axis flux, q-axis torque), solvable with simple PI controllers.

### The full control loop

```
  Phase        Clarke       Park        PI          Inv Park      SVPWM       PWM
 Currents    Transform   Transform   Controllers   Transform    Generator   Outputs
 Ia,Ib,Ic --> Iα,Iβ --> Id,Iq --> Vd,Vq --> Vα,Vβ --> Ta,Tb,Tc --> dutyA,B,C
                  ^           ^       |                     ^
                  |           |       |                     |
                  |      sin(θ),cos(θ)                     |
                  |           |                            Vdc
                  |        θ (rotor electrical angle)
                  |
           Ic = -(Ia+Ib)  (if only 2 phases measured)
```

Inputs to the coprocessor (written by CPU via registers):
- `Ia`, `Ib`: measured phase currents (digital, from external ADC)
- `theta`: rotor electrical angle (digital, from external encoder)
- `Id_ref`, `Iq_ref`: current reference commands
- `Kp`, `Ki`: PI controller gains
- `Vdc`: DC bus voltage (for SVPWM normalization)

Outputs from the coprocessor (read by CPU or directly driving PWM):
- `duty_a`, `duty_b`, `duty_c`: PWM duty cycles for three half-bridges

### 1.1 Clarke Transform (abc → αβ)

The Clarke transform projects three-phase quantities into a two-axis stationary orthogonal frame. We use the **amplitude-invariant** form (2/3 scaling), which preserves physical current magnitudes and simplifies PI tuning.

Full matrix form:

```
[Iα]       [ 1    -1/2       -1/2      ] [Ia]
[Iβ] = 2/3 [ 0     sqrt(3)/2  -sqrt(3)/2] [Ib]
[I0]       [ 1/2   1/2        1/2      ] [Ic]
```

For a balanced three-phase system, `Ia + Ib + Ic = 0`, so `I0 = 0`. Using `Ic = -(Ia + Ib)`, the transform reduces to two equations with two inputs:

```
Iα = Ia
Iβ = (1/sqrt(3)) * Ia + (2/sqrt(3)) * Ib
   = (1/sqrt(3)) * (Ia + 2*Ib)
```

**Hardware form** (avoiding division, using only multiply/shift):

```
Iα = Ia
Iβ = (Ia + 2*Ib) * INV_SQRT3
```

where `INV_SQRT3 = 1/sqrt(3) ≈ 0.57735`. In Q1.15 fixed-point: `INV_SQRT3 = round(0.57735 * 32768) = 18919 = 0x49E7`.

Alternatively, `sqrt(3)` can be approximated multiplier-free via shift-and-add (as in the FPGA-FOC reference):

```
sqrt(3) ≈ 1 + 1/2 + 1/8 + 1/16 + 1/32 + 1/128 + 1/256 + 1/1024 + 1/2048
        = 1.732421875  (error: -0.00008, or 0.005%)
```

Then `Iβ = (Ib - Ic) * sqrt(3)` using the shift-add chain, and divide by 2 (right-shift by 1) if needed.

### 1.2 Park Transform (αβ → dq)

The Park transform rotates the stationary αβ frame to a frame aligned with the rotor flux, using the electrical angle `θ`:

```
[Id]   [ cos(θ)   sin(θ)] [Iα]
[Iq] = [-sin(θ)   cos(θ)] [Iβ]
```

Scalar equations:

```
Id =  Iα * cos(θ) + Iβ * sin(θ)
Iq = -Iα * sin(θ) + Iβ * cos(θ)
```

This requires computing `sin(θ)` and `cos(θ)` — see Section 3 (CORDIC).

The electrical angle `θ` relates to the mechanical angle `θ_m` by:

```
θ = P * θ_m
```

where `P` is the number of rotor pole pairs.

### 1.3 PI Controllers (d-axis and q-axis)

In the rotating dq frame, Id and Iq are DC quantities at steady state. Two independent PI controllers regulate them:

**Continuous-time:**

```
Vd(t) = Kp * ed(t) + Ki * integral(ed(τ) dτ)     where ed = Id_ref - Id
Vq(t) = Kp * eq(t) + Ki * integral(eq(τ) dτ)     where eq = Iq_ref - Iq
```

**Discrete-time (forward Euler, suitable for hardware):**

```
e[k]   = ref - measured
u_p[k] = Kp * e[k]                                 // proportional term
u_i[k] = u_i[k-1] + Ki * Ts * e[k]                // integral term
u[k]   = u_p[k] + u_i[k]                          // total output
```

where `Ts` is the control period (= 1 / FOC update rate).

In hardware, we absorb `Ts` into `Ki` (the CPU pre-scales `Ki` by `Ts`), so the update becomes:

```
e[k]   = ref - measured
u_i[k] = u_i[k-1] + Ki_scaled * e[k]
u[k]   = Kp * e[k] + u_i[k]
```

**Anti-windup (clamping method):**

When the output saturates at voltage limits, the integrator must freeze to prevent windup:

```
u_raw = Kp * e[k] + u_i_tentative
u_sat = clamp(u_raw, -U_MAX, +U_MAX)

if (u_sat != u_raw):                              // output is saturated
    if sign(e[k]) == sign(u_raw):                  // error would push further into saturation
        u_i[k] = u_i[k-1]                         // freeze integrator
    else:
        u_i[k] = u_i_tentative                    // allow recovery
else:
    u_i[k] = u_i_tentative                        // normal operation
```

A simpler hardware-friendly approach: **clamp the integrator independently:**

```
u_i_tentative = u_i[k-1] + Ki_scaled * e[k]
u_i[k] = clamp(u_i_tentative, -I_MAX, +I_MAX)     // integrator clamp
u[k]   = clamp(Kp * e[k] + u_i[k], -U_MAX, +U_MAX) // output clamp
```

### 1.4 Inverse Park Transform (dq → αβ)

The inverse Park transform rotates the voltage commands back to the stationary frame:

```
[Vα]   [cos(θ)  -sin(θ)] [Vd]
[Vβ] = [sin(θ)   cos(θ)] [Vq]
```

Scalar equations:

```
Vα = Vd * cos(θ) - Vq * sin(θ)
Vβ = Vd * sin(θ) + Vq * cos(θ)
```

Note: the same `sin(θ)` and `cos(θ)` values computed for the forward Park transform are reused here, so only one sin/cos evaluation per FOC iteration is needed.

### 1.5 Space Vector PWM (SVPWM)

SVPWM maps the αβ voltage vector to three-phase PWM duty cycles, achieving ~15% better DC bus utilization than sinusoidal PWM (modulation index up to `2/sqrt(3) ≈ 1.155`).

#### Sector determination

The voltage hexagon has 6 sectors. Sector is determined without `atan2` using sign tests:

```
Uref1 = Vβ
Uref2 = (sqrt(3) * Vα - Vβ) / 2
Uref3 = (-sqrt(3) * Vα - Vβ) / 2

A = 1 if Uref1 > 0, else 0
B = 1 if Uref2 > 0, else 0
C = 1 if Uref3 > 0, else 0

N = 4*C + 2*B + A
```

Sector lookup from N:

```
N:       1 → sector II
N:       2 → sector VI
N:       3 → sector I
N:       4 → sector IV
N:       5 → sector III
N:       6 → sector V
```

This is a simple 3-bit to 3-bit lookup table in hardware.

#### Intermediate timing variables

```
X = sqrt(3) * Tpwm / Vdc * Vβ
Y = Tpwm / Vdc * (3/2 * Vα + sqrt(3)/2 * Vβ)
Z = Tpwm / Vdc * (-3/2 * Vα + sqrt(3)/2 * Vβ)
```

In hardware, we normalize voltages to Vdc and Tpwm at the register interface, so the CPU writes pre-scaled values. The simplified hardware equations (with Vα, Vβ pre-normalized to `[-1, +1]` relative to Vdc):

```
X = sqrt(3) * Vβ_norm
Y = 3/2 * Vα_norm + sqrt(3)/2 * Vβ_norm
Z = -3/2 * Vα_norm + sqrt(3)/2 * Vβ_norm
```

#### Active vector times T1, T2 per sector

| Sector | T1   | T2   |
|--------|------|------|
| I      | Z    | Y    |
| II     | Y    | -X   |
| III    | -Z   | X    |
| IV     | -X   | Z    |
| V      | X    | -Y   |
| VI     | -Y   | -Z   |

All times are in units of PWM counts (0 to `Tpwm`).

**Overmodulation clamp:**

```
if T1 + T2 > Tpwm:
    scale = Tpwm / (T1 + T2)
    T1 = T1 * scale
    T2 = T2 * scale
```

**Zero vector time:**

```
T0 = Tpwm - T1 - T2
```

#### Phase duty cycle assignment (center-aligned, 7-segment)

Compute switching times:

```
ta = (Tpwm - T1 - T2) / 4     // quarter of zero-vector time
tb = ta + T1 / 2
tc = tb + T2 / 2
```

Assign compare values per sector:

| Sector | Phase A | Phase B | Phase C |
|--------|---------|---------|---------|
| I      | tb      | ta      | tc      |
| II     | ta      | tc      | tb      |
| III    | ta      | tb      | tc      |
| IV     | tc      | tb      | ta      |
| V      | tc      | ta      | tb      |
| VI     | tb      | tc      | ta      |

Duty cycle for each phase: `duty = 1.0 - 2 * Tcmp / Tpwm` (lower Tcmp → higher duty).

#### Alternative: min-max injection method

Mathematically equivalent to SVPWM, avoids sector lookup entirely:

```
Va_ref = Vα
Vb_ref = -Vα/2 + sqrt(3)/2 * Vβ
Vc_ref = -Vα/2 - sqrt(3)/2 * Vβ

Voffset = -(max(Va_ref, Vb_ref, Vc_ref) + min(Va_ref, Vb_ref, Vc_ref)) / 2

duty_a = 0.5 + (Va_ref + Voffset) / Vdc
duty_b = 0.5 + (Vb_ref + Voffset) / Vdc
duty_c = 0.5 + (Vc_ref + Voffset) / Vdc
```

This is simpler to implement in hardware (needs a 3-input min/max comparator instead of sector logic) and produces identical PWM waveforms.

## 2. Fixed-Point Representation

### 2.1 Format Definition

The coprocessor uses signed two's-complement fixed-point with parameterizable word width `DATA_W` and fractional bits `FRAC_W`. The default is Q1.15 (16-bit) for the datapath and wider accumulators for the PI integrator.

```
Bit layout (DATA_W-bit signed fixed-point, FRAC_W fractional bits):
  [DATA_W-1]            sign bit
  [DATA_W-2 : FRAC_W]  integer part (DATA_W - FRAC_W - 1 magnitude bits)
  [FRAC_W-1 : 0]       fractional part

Value = raw_signed_integer / 2^FRAC_W

INT_W = DATA_W - FRAC_W    // total integer bits including sign
```

### 2.2 Default Configuration: Q1.15 (DATA_W=16, FRAC_W=15)

```
Range:      -1.0 to +0.999969  (= 1 - 2^-15)
Resolution: 2^-15 = 0.0000305
Hex for +1.0: 0x7FFF (closest representable: +0.999969)
Hex for -1.0: 0x8000 (exactly representable)
Hex for  0.0: 0x0000
```

Q1.15 is the standard format for motor control because:
- Phase currents are normalized to the sensor full-scale range `[-1, +1]`
- Sin/cos values lie in `[-1, +1]`
- Voltages are normalized to Vdc: `[-1, +1]`

### 2.3 Signal Formats Throughout the Pipeline

| Signal | Format | Range | Notes |
|--------|--------|-------|-------|
| Phase currents Ia, Ib | Q`INT_W`.`FRAC_W` | ±full-scale | Normalized by CPU before write |
| Iα, Iβ | Q`INT_W`.`FRAC_W` | ±full-scale | Same range as phase currents |
| sin(θ), cos(θ) | Q1.`FRAC_W` | [-1, +1) | CORDIC output, always unit range |
| Id, Iq | Q`INT_W`.`FRAC_W` | ±full-scale | Park transform output |
| Id_ref, Iq_ref | Q`INT_W`.`FRAC_W` | ±full-scale | Reference commands |
| Error (e) | Q`INT_W`.`FRAC_W` | ±2×full-scale | Ref - measured, needs guard bit |
| Kp, Ki | Q`INT_W`.`FRAC_W` | ±full-scale | Pre-scaled by CPU |
| PI accumulator | Q`PI_INT_W`.`PI_FRAC_W` | wide | `PI_ACC_W` bits total |
| Vd, Vq | Q`INT_W`.`FRAC_W` | ±Vdc | Clamped by anti-windup |
| Vα, Vβ | Q`INT_W`.`FRAC_W` | ±Vdc | Inverse Park output |
| Duty cycles | Unsigned, `PWM_BITS` | [0, 2^PWM_BITS) | PWM compare values |
| Electrical angle θ | Unsigned, `ANGLE_W` | [0, 2^ANGLE_W) | Full circle = full range |

### 2.4 Angle Representation

The electrical angle uses an unsigned integer where the full range `[0, 2^ANGLE_W)` maps to `[0, 2π)`:

```
ANGLE_W = 16 (default)

0x0000 =   0° (0 rad)
0x4000 =  90° (π/2 rad)
0x8000 = 180° (π rad)
0xC000 = 270° (3π/2 rad)
0xFFFF ≈ 360° (wraps to 0)
```

This representation has a critical advantage: **unsigned addition/subtraction naturally wraps modulo 2π**. Computing `θ + Δθ` is just unsigned integer addition with no range checking.

### 2.5 Multiplication and Truncation

When multiplying two Q1.15 values:

```
Product of two Q1.15 values:
  16-bit × 16-bit → 32-bit result in Q2.30 format
  To return to Q1.15: arithmetic right-shift by 15 (keep upper 17 bits, take lower 16)

General: Q(A).(B) × Q(C).(D) → Q(A+C).(B+D)
  Truncate back to Q(A).(B) by right-shifting by D bits
```

**Special case:** `-1.0 × -1.0 = +1.0`, which overflows Q1.15. Hardware must either saturate to `0x7FFF` (+0.999969) or use a guard bit.

### 2.6 Rounding

Two modes (selected by parameter `ROUND_MODE`):
- **Truncation** (mode 0): discard low bits. Biased toward negative. Zero hardware cost.
- **Convergent rounding** (mode 1): add `0.5 LSB` before truncation, tie-break to even. Unbiased. Costs one adder per truncation point.

Default: truncation (saves area; the control loop's feedback nullifies DC bias).

## 3. CORDIC Algorithm for Sin/Cos

### 3.1 Overview

The CORDIC (COordinate Rotation DIgital Computer) algorithm computes trigonometric functions using only shifts and additions — no multiplier required. It iteratively rotates a vector toward the target angle using successively smaller rotation steps.

For FOC, we use **circular rotation mode** to compute `sin(θ)` and `cos(θ)` from the input angle `θ`.

### 3.2 Iterative Equations

Initialize:

```
x[0] = 1/K  (pre-compensated CORDIC gain)
y[0] = 0
z[0] = θ    (target angle)
```

At each iteration `i = 0, 1, ..., N_ITER-1`:

```
d[i] = +1 if z[i] >= 0, else -1     (sign of remaining angle)

x[i+1] = x[i] - d[i] * (y[i] >> i)
y[i+1] = y[i] + d[i] * (x[i] >> i)
z[i+1] = z[i] - d[i] * ATAN_TABLE[i]
```

After convergence (`z ≈ 0`):

```
x[N] ≈ cos(θ)
y[N] ≈ sin(θ)
```

The `>> i` operations are arithmetic right-shifts, requiring no multiplier.

### 3.3 CORDIC Gain Factor K

Each iteration scales the vector magnitude by `sqrt(1 + 2^(-2i))`. The cumulative gain is:

```
K = product(i=0..N-1) sqrt(1 + 2^(-2i))
```

| Iterations | K            | 1/K          |
|------------|--------------|--------------|
| 8          | 1.646693     | 0.607259     |
| 12         | 1.646756     | 0.607254     |
| 16         | 1.646760     | 0.607253     |
| 20         | 1.646760     | 0.607253     |
| ∞          | 1.646760258  | 0.607252935  |

Pre-compensation: initialize `x[0] = 1/K` so the output vectors have unit magnitude.

Fixed-point values for `1/K`:

```
Q1.15:  round(0.607253 * 32768) = 19898 = 0x4DBA
Q1.31:  round(0.607253 * 2^31) = 1304969831 = 0x4DBA76D4
Q8.24:  round(0.607253 * 2^24) = 10185433 = 0x9B74E9
```

### 3.4 Arctangent Lookup Table

The CORDIC algorithm requires a table of `arctan(2^(-i))` values:

| i  | arctan(2^-i) radians | 16-bit angle (full-circle = 2^16) |
|----|---------------------|-----------------------------------|
| 0  | 0.785398            | 8192                              |
| 1  | 0.463648            | 4836                              |
| 2  | 0.244979            | 2555                              |
| 3  | 0.124355            | 1297                              |
| 4  | 0.062419            | 651                               |
| 5  | 0.031240            | 326                               |
| 6  | 0.015624            | 163                               |
| 7  | 0.007812            | 81                                |
| 8  | 0.003906            | 41                                |
| 9  | 0.001953            | 20                                |
| 10 | 0.000977            | 10                                |
| 11 | 0.000488            | 5                                 |
| 12 | 0.000244            | 3                                 |
| 13 | 0.000122            | 1                                 |
| 14 | 0.000061            | 1                                 |
| 15 | 0.000031            | 0                                 |

The 16-bit angle values are computed as: `round(arctan(2^-i) / (2π) * 65536)`.

This table is a small ROM (16 entries × ANGLE_W bits) hardwired in the RTL.

### 3.5 Quadrant Pre-Rotation

CORDIC rotation mode converges only for `|θ| ≤ sum(arctan(2^-i)) ≈ 99.88°`. To handle the full 360° range, quadrant pre-rotation is applied using the top 2 bits of the angle:

```
quadrant = θ[ANGLE_W-1 : ANGLE_W-2]

case quadrant:
  00 (0°-90°):    z0 = θ,           x_init = +1/K, y_init = 0
  01 (90°-180°):  z0 = θ - 90°,     x_init = 0,    y_init = +1/K
                  post: cos = -y_out, sin = +x_out
  10 (180°-270°): z0 = θ - 180°,    x_init = +1/K, y_init = 0
                  post: cos = -x_out, sin = -y_out
  11 (270°-360°): z0 = θ - 270°,    x_init = 0,    y_init = +1/K
                  post: cos = +y_out, sin = -x_out
```

Alternatively, a simpler approach: fold to Q1 (`[0°, 90°]`) and apply sign/swap corrections afterward:

```
// Map any angle to first quadrant
angle_q1 = θ[ANGLE_W-3:0]                          // strip top 2 bits
if θ[ANGLE_W-2]: angle_q1 = ~angle_q1              // mirror within half-circle

// Run CORDIC on angle_q1 → (cos_q1, sin_q1)

// Reconstruct full-circle result
cos_out = (θ[ANGLE_W-2]) ? -cos_q1 : +cos_q1       // negate cos in Q2/Q3
sin_out = (θ[ANGLE_W-1]) ? -sin_q1 : +sin_q1       // negate sin in Q3/Q4

// Handle Q2 (90°-180°) and Q4 (270°-360°): swap sin/cos
if θ[ANGLE_W-2] XOR θ[ANGLE_W-1]: swap(cos_out, sin_out)
```

### 3.6 Precision vs Iterations

CORDIC converges approximately 1 bit per iteration:

```
Angle error after N iterations: |z[N]| ≤ arctan(2^(-N))

For N = 12: error ≤ 0.000244 rad ≈ 0.014° → ~12-bit accuracy
For N = 16: error ≤ 0.0000153 rad ≈ 0.001° → ~16-bit accuracy
For N = DATA_W: error ≤ 1 LSB of angle representation
```

Rule of thumb: use `CORDIC_ITERS = FRAC_W` for full-precision sin/cos matching the datapath word width.

### 3.7 Alternative: LUT with Interpolation

For designs where area (ROM) is cheaper than latency, a lookup table approach:

```
Table: sin_lut[0..T-1] covering [0, π/2) in T entries, FRAC_W-bit values
Size: T entries × FRAC_W bits (e.g., 1024 × 16 = 2 KB for one quadrant)

Lookup:
  addr = θ[ANGLE_W-3 : ANGLE_W-2-log2(T)]   // table index
  frac = θ[remaining low bits]                 // interpolation fraction
  sin_val = sin_lut[addr] + frac * (sin_lut[addr+1] - sin_lut[addr]) >> frac_bits

Latency: 2-3 cycles (lookup + interpolation)
Accuracy: ~14 bits with T=1024 and linear interpolation
```

The coprocessor supports both CORDIC and LUT via the `SINCOS_MODE` parameter.

## 4. SVPWM Detailed Derivation

### 4.1 Voltage Space Vectors

A three-phase two-level inverter has 8 states (each phase is either connected to +Vdc/2 or -Vdc/2). Six states produce active vectors; two produce zero vectors:

```
        V3 (010)
         /    \
   V2 (110)    V4 (011)
     |    V0,V7   |
   V6 (100)    V5 (001)
         \    /
        V1 (101)

Active vectors: V1..V6, magnitude = (2/3)*Vdc, spaced 60° apart
Zero vectors:   V0 = (000), V7 = (111), magnitude = 0
```

Vector angles:

```
V1 (100): 0°
V2 (110): 60°
V3 (010): 120°
V4 (011): 180°
V5 (001): 240°
V6 (101): 300°
```

### 4.2 Duty Cycle Synthesis

The reference voltage vector `V_ref = (Vα, Vβ)` is synthesized by time-averaging adjacent active vectors and zero vectors within one PWM period:

```
V_ref * Tpwm = V_a * T1 + V_b * T2 + V_0 * T0
```

where `V_a` and `V_b` are the two active vectors bounding the sector containing `V_ref`, `T1` and `T2` are their on-times, and `T0` is the zero-vector time.

### 4.3 Sector Determination (Hardware-Friendly)

Computing three sign tests avoids atan2:

```
// Using pre-computed sqrt(3)*Vα (one multiply with constant)
u1 = Vβ
u2 = (SQRT3 * Vα - Vβ) >> 1         // = (sqrt(3)*Vα - Vβ) / 2
u3 = (-SQRT3 * Vα - Vβ) >> 1        // = (-sqrt(3)*Vα - Vβ) / 2

A = (u1 > 0) ? 1 : 0
B = (u2 > 0) ? 1 : 0
C = (u3 > 0) ? 1 : 0

sector = SECTOR_LUT[{C, B, A}]      // 8-entry LUT
```

SECTOR_LUT contents:

```
Index [C,B,A] = 0b001 (1) → sector 2
Index [C,B,A] = 0b010 (2) → sector 6
Index [C,B,A] = 0b011 (3) → sector 1
Index [C,B,A] = 0b100 (4) → sector 4
Index [C,B,A] = 0b101 (5) → sector 3
Index [C,B,A] = 0b110 (6) → sector 5
Index [C,B,A] = 0b000 (0) → invalid (zero vector)
Index [C,B,A] = 0b111 (7) → invalid (zero vector)
```

### 4.4 Active Vector Times

Define normalized intermediate values (all expressed in PWM counts):

```
// PWM_SCALE = Tpwm * sqrt(3) / Vdc  (pre-computed, stored in register)
X = SQRT3 * Vβ_scaled                  // sqrt(3) * Vβ * Tpwm / Vdc
Y = (3 * Vα_scaled + SQRT3 * Vβ_scaled) >> 1
Z = (-3 * Vα_scaled + SQRT3 * Vβ_scaled) >> 1
```

Per-sector T1/T2 assignment (from the table in Section 1.5).

### 4.5 Min-Max Injection (Preferred Hardware Implementation)

The min-max method is mathematically equivalent to SVPWM and avoids the sector lookup and per-sector T1/T2 switching:

```
// Inverse Clarke to get three-phase reference voltages
Va = Vα
Vb = (-Vα + SQRT3 * Vβ) >> 1          // (-Vα + sqrt(3)*Vβ) / 2
Vc = (-Vα - SQRT3 * Vβ) >> 1          // (-Vα - sqrt(3)*Vβ) / 2

// Third-harmonic injection via min-max
Vmax = max(Va, Vb, Vc)
Vmin = min(Va, Vb, Vc)
Voffset = -(Vmax + Vmin) >> 1          // = -(max + min) / 2

// Duty cycles (centered at 50%)
duty_a = HALF_SCALE + ((Va + Voffset) * PWM_MAX) >> (FRAC_W + 1)
duty_b = HALF_SCALE + ((Vb + Voffset) * PWM_MAX) >> (FRAC_W + 1)
duty_c = HALF_SCALE + ((Vc + Voffset) * PWM_MAX) >> (FRAC_W + 1)
```

where `HALF_SCALE = 2^(PWM_BITS-1)` and `PWM_MAX = 2^PWM_BITS - 1`.

The 3-input min/max requires just two comparators per operation (6 comparators total for both min and max), which is trivial in hardware.

## 5. Error Budget Analysis

### 5.1 Quantization Error Sources

| Source | Error per sample | Accumulates? | Notes |
|--------|-----------------|--------------|-------|
| Input current quantization | 2^(-FRAC_W) | No | One-time truncation from ADC |
| Sin/cos (CORDIC, N iters) | 2^(-(N-1)) | No | Each evaluation independent |
| Sin/cos (LUT, T entries) | (π/(2T))^2 / 8 | No | Interpolation error |
| Multiply truncation | 2^(-FRAC_W) per mul | Weakly | 4 multiplies in Park+invPark |
| PI integrator | 2^(-PI_FRAC_W) per step | Yes | But feedback loop corrects |

### 5.2 Clarke Transform Error

The Clarke transform involves one multiplication (by `1/sqrt(3)`) and additions. With Q1.15:

```
Multiplication error: ≤ 1 LSB = 2^-15 ≈ 3.05e-5
Addition error: 0 (exact for same-format addition with sufficient guard bits)

Total Clarke error: ≤ 1 LSB per output component
```

### 5.3 Park Transform Error

Two multiplications and one addition per output:

```
Id = Iα*cos(θ) + Iβ*sin(θ)

Each multiply: product error ≤ 2^(-FRAC_W) (truncation) + sin/cos error
Addition of two products: ≤ 2 * 2^(-FRAC_W)

Sin/cos error with CORDIC (FRAC_W iterations): ≤ 2^(-FRAC_W)
Sin/cos error with LUT (1024 entries): ≤ 4.7e-6 ≈ 2^(-17.7)

Total Park error per component: ≤ 3 * 2^(-FRAC_W) (CORDIC) or ≤ 2 * 2^(-FRAC_W) (LUT)
```

For Q1.15 with 16-iteration CORDIC: total Park error ≤ ~3 LSB per component.

### 5.4 PI Controller Error

The PI integrator accumulates rounding errors over time, but the **closed-loop feedback** nullifies DC error. The steady-state error contribution from quantization is:

```
Steady-state current error ≈ 2^(-PI_FRAC_W) / Ki
```

With `PI_ACC_W = 32` and `PI_FRAC_W = 24`, the integrator has ~24 bits of fractional precision, giving negligible steady-state error for any reasonable Ki.

**Overflow analysis:** The integrator accumulates at most one `Ki * e[k]` per cycle. With PI_ACC_W = 32 bits and inputs bounded to ±1.0 (Q1.15), overflow requires:

```
Max accumulation per step: |Ki| * |e_max| ≤ 1.0 * 2.0 = 2.0  (in Q1.15 units)
Steps to overflow 32-bit accumulator: 2^(PI_ACC_W - FRAC_W - 2) = 2^15 = 32768 steps
At 20 kHz: 1.6 seconds — anti-windup clamp prevents this.
```

### 5.5 SVPWM Error

Duty cycle quantization:

```
PWM_BITS = 10: 1024 levels, duty resolution = 0.098% → ±0.05% duty error
PWM_BITS = 12: 4096 levels, duty resolution = 0.024% → ±0.012% duty error

Voltage error = duty_error * Vdc
For Vdc = 12V, PWM_BITS = 10: voltage error ≤ ±6 mV per phase
For Vdc = 24V, PWM_BITS = 12: voltage error ≤ ±3 mV per phase
```

### 5.6 End-to-End Error Budget (Default: DATA_W=16, FRAC_W=15, CORDIC_ITERS=16, PWM_BITS=10)

| Stage | Max error (LSBs of Q1.15) | Equivalent |
|-------|--------------------------|------------|
| Clarke | 1 | 3.05e-5 |
| Sin/cos (CORDIC-16) | 1 | 3.05e-5 |
| Park | 3 | 9.15e-5 |
| PI (single step) | 1 | 3.05e-5 |
| Inverse Park | 3 | 9.15e-5 |
| SVPWM duty quantization | N/A | 0.098% of Vdc |
| **Total (worst case, all add)** | **~9 LSBs** | **~2.7e-4** |

This is approximately 72 dB SNR, well within requirements for motor control (where mechanical/electrical noise dominates).

## 6. Test Vectors

### 6.1 Clarke Transform

**Test 1: Balanced unity current on phase A**

```
Input:  Ia = +1.0 (0x7FFF), Ib = -0.5 (0xC000), Ic = -0.5 (0xC000)
Expected: Iα = +1.0, Iβ = 0.0
Verify:   Iα = Ia = 1.0 ✓
          Iβ = (1.0 + 2*(-0.5)) / sqrt(3) = 0 / sqrt(3) = 0.0 ✓
```

**Test 2: Balanced current at 90° electrical**

```
Input:  Ia = 0.0, Ib = +sqrt(3)/2 ≈ +0.866, Ic = -sqrt(3)/2 ≈ -0.866
Expected: Iα = 0.0, Iβ = +1.0
Verify:   Iα = 0.0 ✓
          Iβ = (0 + 2*0.866) / sqrt(3) = 1.732 / 1.732 = 1.0 ✓
```

**Test 3: Equal currents (zero output)**

```
Input:  Ia = +0.5, Ib = +0.5, Ic = -1.0
Expected: Iα = +0.5, Iβ = (0.5 + 1.0) / sqrt(3) ≈ +0.866
```

### 6.2 Park Transform

**Test 1: θ = 0° (identity rotation)**

```
Input:  Iα = +0.5, Iβ = +0.866, θ = 0x0000
Expected: Id = +0.5 * cos(0) + 0.866 * sin(0) = +0.5
          Iq = -0.5 * sin(0) + 0.866 * cos(0) = +0.866
```

**Test 2: θ = 90° (full quadrant rotation)**

```
Input:  Iα = +1.0, Iβ = 0.0, θ = 0x4000 (90°)
Expected: cos(90°) = 0, sin(90°) = 1
          Id = 1.0 * 0 + 0 * 1 = 0.0
          Iq = -1.0 * 1 + 0 * 0 = -1.0
```

**Test 3: θ = 30°**

```
Input:  Iα = +0.75, Iβ = +0.25, θ = 0x1555 (30° = 65536/12)
Expected: cos(30°) = 0.866, sin(30°) = 0.5
          Id = 0.75 * 0.866 + 0.25 * 0.5 = 0.6495 + 0.125 = 0.7745
          Iq = -0.75 * 0.5 + 0.25 * 0.866 = -0.375 + 0.2165 = -0.1585
```

### 6.3 PI Controller

**Test 1: Step response (Kp only)**

```
Setup:  Kp = 0.5 (0x4000 Q1.15), Ki = 0, ref = +0.8, measured = 0.0
Step 0: e = 0.8, u = 0.5 * 0.8 = 0.4
Step 1: (measured still 0): u = 0.4 (same)
```

**Test 2: Integrator accumulation**

```
Setup:  Kp = 0, Ki_scaled = 0.1 (0x0CCC Q1.15), ref = +0.5, measured = 0.0
Step 0: e = 0.5, u_i = 0 + 0.1*0.5 = 0.05, u = 0.05
Step 1: e = 0.5, u_i = 0.05 + 0.05 = 0.10, u = 0.10
Step 2: e = 0.5, u_i = 0.10 + 0.05 = 0.15, u = 0.15
...
Step 19: u_i = 1.0 → clamped to U_MAX
```

**Test 3: Anti-windup**

```
Setup:  Kp = 0.5, Ki_scaled = 0.1, U_MAX = 0.9, ref = +1.0, measured = 0.0
Step 0: e = 1.0, u_p = 0.5, u_i = 0.1, u_raw = 0.6, u = 0.6
...
Step 4: u_i = 0.5, u_raw = 1.0 > U_MAX → clamp output to 0.9, freeze integrator
Step 5: (if error still positive) u_i stays at 0.5, u = clamp(0.5 + 0.5) = 0.9
```

### 6.4 CORDIC Sin/Cos

**Test 1: θ = 0°**

```
Input:  θ = 0x0000
Expected: cos = +1.0 (0x7FFF), sin = 0.0 (0x0000)
```

**Test 2: θ = 45°**

```
Input:  θ = 0x2000 (45° = 65536/8)
Expected: cos = sin = sqrt(2)/2 ≈ 0.70711
          Q1.15: round(0.70711 * 32768) = 23170 = 0x5A82
```

**Test 3: θ = 90°**

```
Input:  θ = 0x4000
Expected: cos = 0.0 (0x0000), sin = +1.0 (0x7FFF)
```

**Test 4: θ = 150° (quadrant 2)**

```
Input:  θ = 0x6AAA (150° = 65536 * 150/360)
Expected: cos = -sqrt(3)/2 ≈ -0.866 → 0x9127 in Q1.15
          sin = +0.5 → 0x4000 in Q1.15
```

**Test 5: θ = 210° (quadrant 3)**

```
Input:  θ = 0x9555
Expected: cos = -sqrt(3)/2 ≈ -0.866, sin = -0.5
```

### 6.5 SVPWM (Min-Max Method)

**Test 1: Vα = 1.0, Vβ = 0.0 (sector I, maximum modulation on A-axis)**

```
Va = 1.0, Vb = -0.5, Vc = -0.5
Vmax = 1.0, Vmin = -0.5, Voffset = -(1.0 + (-0.5))/2 = -0.25
duty_a = 0.5 + (1.0 + (-0.25))/Vdc_norm = 0.5 + 0.375 = 0.875 (for Vdc_norm = 2)
duty_b = 0.5 + (-0.5 + (-0.25))/2 = 0.5 - 0.375 = 0.125
duty_c = 0.5 + (-0.5 + (-0.25))/2 = 0.5 - 0.375 = 0.125
```

**Test 2: Vα = 0.0, Vβ = 0.0 (zero vector)**

```
Va = 0, Vb = 0, Vc = 0
Vmax = Vmin = 0, Voffset = 0
duty_a = duty_b = duty_c = 0.5 (50% duty, zero net voltage)
```

**Test 3: Vα = 0.5, Vβ = 0.5 (sector I, partial modulation)**

```
Va = 0.5
Vb = (-0.5 + sqrt(3)*0.5)/2 = (-0.5 + 0.866)/2 = 0.183
Vc = (-0.5 - 0.866)/2 = -0.683
Vmax = 0.5, Vmin = -0.683, Voffset = -(0.5 + (-0.683))/2 = 0.0915
duty_a = 0.5 + (0.5 + 0.0915)/2 = 0.7958
duty_b = 0.5 + (0.183 + 0.0915)/2 = 0.6373
duty_c = 0.5 + (-0.683 + 0.0915)/2 = 0.2043
```

### 6.6 Full Pipeline Test

**Scenario: Motor at θ=30°, Iq_ref=0.5, Id_ref=0 (first iteration, PI integrators at zero)**

```
Step 1 - Given: Ia = 0.45, Ib = 0.10 (measured currents after ADC scaling)

Step 2 - Clarke:
  Iα = 0.45
  Iβ = (0.45 + 0.20) / sqrt(3) = 0.375

Step 3 - Park (θ = 30°, cos=0.866, sin=0.5):
  Id = 0.45*0.866 + 0.375*0.5 = 0.390 + 0.188 = 0.578
  Iq = -0.45*0.5 + 0.375*0.866 = -0.225 + 0.325 = 0.100

Step 4 - PI (Kp=0.5, first iteration so u_i=0):
  ed = 0 - 0.578 = -0.578 → Vd = 0.5 * (-0.578) = -0.289
  eq = 0.5 - 0.100 = 0.400 → Vq = 0.5 * 0.400 = 0.200

Step 5 - Inverse Park (θ = 30°):
  Vα = (-0.289)*0.866 - 0.200*0.5 = -0.250 - 0.100 = -0.350
  Vβ = (-0.289)*0.5 + 0.200*0.866 = -0.145 + 0.173 = 0.029

Step 6 - SVPWM (min-max):
  Va = -0.350
  Vb = (-(-0.350) + sqrt(3)*0.029)/2 = (0.350 + 0.050)/2 = 0.200
  Vc = (0.350 - 0.050)/2 = 0.150
  Vmax = 0.200, Vmin = -0.350, Voffset = 0.075
  duty_a = 0.5 + (-0.350 + 0.075)/2 = 0.3625
  duty_b = 0.5 + (0.200 + 0.075)/2 = 0.6375
  duty_c = 0.5 + (0.150 + 0.075)/2 = 0.6125
```

These duty cycles should be verified bit-exactly against the RTL simulation using the same fixed-point format and rounding mode.
