# Microarchitecture Specification

## 1. Parameter Space

The coprocessor is parameterized across ten axes. All parameters are defined in `rtl/foc_pkg.sv` and propagate from `foc_top` down to leaf modules. The AutoSilicon optimization loop sweeps these parameters to find Pareto-optimal configurations across area, Fmax, and precision.

| Parameter | RTL Name | Legal Values | Default | Affects |
|-----------|----------|-------------|---------|---------|
| Word width | `DATA_W` | 12, 16, 24, 32 | 16 | Multiplier area (quadratic), precision, range |
| Fractional bits | `FRAC_W` | 7..DATA_W-1 | 15 | Fixed-point resolution, range tradeoff |
| CORDIC iterations | `CORDIC_ITERS` | 8, 10, 12, 14, 16, 20 | 16 | Sin/cos precision (1 bit/iter), latency |
| Sin/cos mode | `SINCOS_MODE` | 0=CORDIC, 1=LUT | 0 | Area vs latency tradeoff |
| LUT depth (if LUT mode) | `SINCOS_LUT_DEPTH` | 256, 512, 1024 | 1024 | ROM area, interpolation accuracy |
| Pipeline depth | `PIPE_DEPTH` | 0, 1, 2, 3 | 1 | Fmax (higher=faster clock) vs latency |
| PWM resolution | `PWM_BITS` | 8, 10, 11, 12 | 10 | Duty cycle precision, counter width |
| Number of phases | `N_PHASES` | 3 | 3 | Parameterized for future N-phase support |
| PI accumulator width | `PI_ACC_W` | 24, 32, 40, 48 | 32 | Integrator headroom, area |
| Shared multiplier | `SHARED_MUL` | 0=DEDICATED, 1=SHARED | 0 | Area vs throughput |
| Angle width | `ANGLE_W` | 12, 16 | 16 | Angle resolution, CORDIC table size |
| Rounding mode | `ROUND_MODE` | 0=TRUNC, 1=CONVERGENT | 0 | Precision vs area (1 adder per trunc point) |

### Derived Parameters

```
INT_W         = DATA_W - FRAC_W               // integer bits (including sign)
PRODUCT_W     = 2 * DATA_W                    // full multiply result width
PI_FRAC_W     = PI_ACC_W - INT_W              // PI accumulator fractional bits
CORDIC_TAB_W  = CORDIC_ITERS * ANGLE_W        // total arctan ROM bits
SINCOS_ROM_SZ = SINCOS_LUT_DEPTH * DATA_W     // LUT ROM size in bits (one quadrant)
PWM_MAX       = (1 << PWM_BITS) - 1           // max PWM counter value
HALF_SCALE    = 1 << (PWM_BITS - 1)           // 50% duty cycle value
```

### Parameter Constraints

```
FRAC_W < DATA_W                               // at least 1 integer bit (sign)
CORDIC_ITERS <= FRAC_W + 2                    // no benefit beyond datapath precision
PI_ACC_W >= DATA_W                            // accumulator at least as wide as datapath
SINCOS_LUT_DEPTH must be power of 2           // for address slicing
```

## 2. Top-Level Block Diagram

```
                              foc_top
  ┌────────────────────────────────────────────────────────────────────┐
  │                                                                    │
  │  Wishbone Bus Interface (32-bit slave)                             │
  │  ┌────────────────────────────────────────────────────────────┐   │
  │  │  wb_slave (foc_wb.sv)                                      │   │
  │  │    - Register decode (CTRL, STATUS, CONFIG, data regs)     │   │
  │  │    - Read/write mux                                        │   │
  │  │    - IRQ generation (done, error)                          │   │
  │  └─────────────────────────┬──────────────────────────────────┘   │
  │                             │ register interface                    │
  │                             v                                      │
  │  ┌──────────────────────────────────────────────────────────────┐ │
  │  │  Control FSM (foc_ctrl.sv)                                   │ │
  │  │    IDLE → SINCOS → CLARKE → PARK → PI_D → PI_Q →            │ │
  │  │    INV_PARK → SVPWM → DONE                                  │ │
  │  │                                                              │ │
  │  │  Drives: stage_sel, operand_mux, result_latch, mul_en       │ │
  │  └──────┬───────────────────────────────────────────────────────┘ │
  │         │ control signals                                         │
  │         v                                                         │
  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
  │  │  CORDIC /    │  │  Datapath    │  │  PI Controllers      │   │
  │  │  SinCos LUT  │  │  (multiply,  │  │  (foc_pi.sv × 2)    │   │
  │  │ (foc_sincos) │  │   add, mux)  │  │                      │   │
  │  │              │  │ (foc_dp.sv)  │  │  - Kp multiply       │   │
  │  │  θ → sin,cos │  │              │  │  - Ki accumulate     │   │
  │  └──────┬───────┘  │  Clarke      │  │  - Anti-windup clamp │   │
  │         │sin,cos   │  Park        │  │  - Output clamp      │   │
  │         └─────────>│  Inv Park    │  │                      │   │
  │                    │  SVPWM       │  └──────────┬───────────┘   │
  │                    └──────┬───────┘             │ Vd, Vq        │
  │                           │ duty_a/b/c          │               │
  │                           v                     │               │
  │                    ┌──────────────┐              │               │
  │                    │  PWM Output  │              │               │
  │                    │  Registers   │<─────────────┘               │
  │                    │              │                               │
  │                    │  duty_a [PWM_BITS-1:0]                      │
  │                    │  duty_b [PWM_BITS-1:0]                      │
  │                    │  duty_c [PWM_BITS-1:0]                      │
  │                    └──────────────┘                               │
  │                                                                    │
  │  External ports:                                                   │
  │    - wb_* (Wishbone slave: cyc, stb, we, adr, dat_i, dat_o, ack) │
  │    - irq (done interrupt)                                         │
  │    - duty_a, duty_b, duty_c [PWM_BITS-1:0] (output registers)    │
  └────────────────────────────────────────────────────────────────────┘
```

The coprocessor is a **pure computational accelerator** — no ADC, encoder, or PWM timer interfaces. The CPU writes sensor data to registers, triggers computation, and reads/uses the resulting duty cycles.

## 3. Pipeline Stages Detail

The FOC computation is organized as a sequential FSM that reuses the shared datapath. Each stage performs specific operations:

### 3.1 Stage Sequence (SHARED_MUL=1, sequential execution)

```
  Cycle  Stage        Operations                              Multiplies  Adds
  ─────  ─────        ──────────                              ──────────  ────
  0      SINCOS       Compute sin(θ), cos(θ)                  0 (CORDIC)  —
                      Latency: CORDIC_ITERS or 3 (LUT)

  C+0    CLARKE       Iα = Ia                                 0-1         1-2
  C+1                 Iβ = (Ia + 2*Ib) * INV_SQRT3            1           1

  C+2    PARK         tmp1 = Iα * cos(θ)                      1           0
  C+3                 tmp2 = Iβ * sin(θ)                      1           0
  C+4                 Id = tmp1 + tmp2                         0           1
  C+5                 tmp3 = Iα * sin(θ)                      1           0
  C+6                 tmp4 = Iβ * cos(θ)                      1           0
  C+7                 Iq = tmp4 - tmp3                         0           1

  C+8    PI_D         ed = Id_ref - Id                         0           1
  C+9                 u_p = Kp * ed                            1           0
  C+10                u_i += Ki_s * ed (accum)                 1           1
  C+11                Vd = clamp(u_p + u_i)                    0           1

  C+12   PI_Q         eq = Iq_ref - Iq                         0           1
  C+13                u_p = Kp * eq                            1           0
  C+14                u_i += Ki_s * eq (accum)                 1           1
  C+15                Vq = clamp(u_p + u_i)                    0           1

  C+16   INV_PARK     tmp5 = Vd * cos(θ)                      1           0
  C+17                tmp6 = Vq * sin(θ)                      1           0
  C+18                Vα = tmp5 - tmp6                         0           1
  C+19                tmp7 = Vd * sin(θ)                      1           0
  C+20                tmp8 = Vq * cos(θ)                      1           0
  C+21                Vβ = tmp7 + tmp8                         0           1

  C+22   SVPWM        Va = Vα (wire)                           0           0
  C+23                Vb = (-Vα + SQRT3*Vβ) >> 1              1           1
  C+24                Vc = (-Vα - SQRT3*Vβ) >> 1              0*          1
                      (* reuse SQRT3*Vβ from C+23)
  C+25                Vmax, Vmin = min/max(Va, Vb, Vc)        0           0
  C+26                Voffset = -(Vmax + Vmin) >> 1           0           1
  C+27                duty_a = HALF + scale(Va+Voff)          1           1
  C+28                duty_b = HALF + scale(Vb+Voff)          1           1
  C+29                duty_c = HALF + scale(Vc+Voff)          1           1

  C+30   DONE         Latch duty_a/b/c, assert IRQ            0           0
```

**Total cycles (shared multiplier):** `CORDIC_ITERS + 31` (= 47 cycles at default config)

### 3.2 Stage Sequence (SHARED_MUL=0, dedicated multipliers)

With dedicated multipliers per stage, Park and inverse Park can execute their 4 multiplies in parallel:

```
  Cycle  Stage        Latency (cycles)
  ─────  ─────        ────────────────
  0      SINCOS       CORDIC_ITERS or 3 (LUT)
  C+0    CLARKE       2
  C+2    PARK         2  (4 muls in 2 cycles using 2 multipliers)
  C+4    PI_D         4
  C+8    PI_Q         4
  C+12   INV_PARK     2  (4 muls in 2 cycles using 2 multipliers)
  C+14   SVPWM        8
  C+22   DONE         1
```

**Total cycles (dedicated):** `CORDIC_ITERS + 23` (= 39 cycles at default config)

### 3.3 Pipeline Registers (PIPE_DEPTH parameter)

When `PIPE_DEPTH > 0`, pipeline registers are inserted after the multiplier and before the adder, allowing higher Fmax at the cost of additional latency per operation:

```
PIPE_DEPTH=0: combinational multiply → add in same cycle (lowest Fmax)
PIPE_DEPTH=1: multiply → register → add (moderate Fmax, +1 cycle per mul-add pair)
PIPE_DEPTH=2: multiply_hi → register → multiply_lo → register → add (highest Fmax)
```

Each pipeline register adds 1 cycle to every operation that passes through the multiplier, increasing total latency by roughly `PIPE_DEPTH * num_multiplications`.

## 4. CORDIC Unit Architecture

### 4.1 Block Diagram

```
                    foc_sincos (SINCOS_MODE=0: CORDIC)
  ┌───────────────────────────────────────────────────────────┐
  │                                                           │
  │  ┌────────────┐     ┌──────────────────────────────────┐ │
  │  │ Quadrant   │     │  CORDIC Core                     │ │
  │  │ Pre-rotate │     │  (iterative, CORDIC_ITERS stages)│ │
  │  │            │     │                                  │ │
  │  │ θ[AW-1:   │     │  x[i+1] = x[i] - d*y[i]>>i     │ │
  │  │   AW-2]   │────>│  y[i+1] = y[i] + d*x[i]>>i     │ │
  │  │ → quad     │     │  z[i+1] = z[i] - d*ATAN[i]     │ │
  │  │ → z0       │     │                                  │ │
  │  │            │     │  Iteration counter: 0..N-1       │ │
  │  └────────────┘     └──────────┬───────────────────────┘ │
  │                                │ x_out, y_out            │
  │                                v                         │
  │                     ┌──────────────────────┐             │
  │                     │ Quadrant Post-correct│             │
  │                     │  Negate / swap based │             │
  │                     │  on original quadrant│             │
  │                     └──────────┬───────────┘             │
  │                                │                         │
  │                                v                         │
  │                          cos_out, sin_out                │
  └───────────────────────────────────────────────────────────┘

  Ports:
    input  start                        // begin computation
    input  [ANGLE_W-1:0] theta          // input angle
    output [DATA_W-1:0] cos_val         // cosine result
    output [DATA_W-1:0] sin_val         // sine result
    output done                         // result valid

  Latency: CORDIC_ITERS + 2 cycles (pre-rotate + iterations + post-correct)
  Resources: 3 adder/subtractors, 1 shifter (barrel or iterative), ATAN ROM
```

### 4.2 CORDIC Registers

```
x_reg  [DATA_W-1:0]     // current x value (initialized to 1/K)
y_reg  [DATA_W-1:0]     // current y value (initialized to 0)
z_reg  [ANGLE_W-1:0]    // remaining angle (initialized to θ mod 90°)
iter   [$clog2(CORDIC_ITERS)-1:0]  // iteration counter
quad   [1:0]             // saved quadrant for post-correction
```

### 4.3 ATAN ROM

Hardwired as a case statement (no RAM needed):

```systemverilog
function automatic [ANGLE_W-1:0] atan_lut(input [$clog2(CORDIC_ITERS)-1:0] i);
  case (i)
    0:  atan_lut = ANGLE_W'(8192);   // arctan(1)     = 45.000°
    1:  atan_lut = ANGLE_W'(4836);   // arctan(1/2)   = 26.565°
    2:  atan_lut = ANGLE_W'(2555);   // arctan(1/4)   = 14.036°
    3:  atan_lut = ANGLE_W'(1297);   // arctan(1/8)   = 7.125°
    ...
    default: atan_lut = ANGLE_W'(0);
  endcase
endfunction
```

### 4.4 LUT Mode (SINCOS_MODE=1)

```
                    foc_sincos (SINCOS_MODE=1: LUT)
  ┌───────────────────────────────────────────────────────────┐
  │                                                           │
  │  ┌────────────┐   ┌───────────────┐   ┌──────────────┐  │
  │  │ Quadrant   │   │  sin_rom      │   │  Linear      │  │
  │  │ fold &     │──>│  [0..T-1]     │──>│  Interpolate │  │
  │  │ addr calc  │   │  (1 quadrant) │   │  (optional)  │  │
  │  └────────────┘   └───────────────┘   └──────┬───────┘  │
  │                                               │          │
  │  ┌────────────┐                        ┌──────┴───────┐  │
  │  │ cos = sin  │                        │ Quadrant     │  │
  │  │ (π/2 - θ) │<───────────────────────│ post-correct │  │
  │  │ (address   │                        │ (sign, swap) │  │
  │  │  offset)   │                        └──────┬───────┘  │
  │  └────────────┘                               │          │
  │                                         cos, sin out     │
  └───────────────────────────────────────────────────────────┘

  Latency: 3 cycles (addr calc → ROM read → interpolate/correct)
  Resources: 1 ROM (T × DATA_W bits), 1 multiplier (for interpolation), 1 adder
  ROM size:  1024 × 16 = 2 KB (one quadrant, default config)
```

Both sin and cos are computed from the same single-quadrant ROM: cos(θ) = sin(π/2 - θ), so a different address offset is used for the second lookup. With a single-port ROM, this requires 2 cycles for both lookups (sequential), or a dual-port ROM for 1-cycle parallel access.

## 5. PI Controller Architecture

### 5.1 Block Diagram

```
                        foc_pi (instantiated twice: d-axis and q-axis)
  ┌────────────────────────────────────────────────────────────────────┐
  │                                                                    │
  │   ref ──────┐                                                      │
  │             ├─ SUB ──> e[k] ──┬──────────────────────────────┐    │
  │   meas ────┘                  │                              │    │
  │                               v                              v    │
  │                        ┌────────────┐               ┌────────────┐│
  │                        │  MUL (Kp)  │               │  MUL (Ki)  ││
  │                        │  Kp × e[k] │               │  Ki × e[k] ││
  │                        └─────┬──────┘               └─────┬──────┘│
  │                              │ u_p                        │ delta │
  │                              │                            v       │
  │                              │                     ┌────────────┐ │
  │                              │                     │  ACC (add) │ │
  │                              │                     │ u_i[k-1]   │ │
  │                              │                     │  + delta   │ │
  │                              │                     └─────┬──────┘ │
  │                              │                           │ u_i_raw│
  │                              │                           v        │
  │                              │                    ┌─────────────┐ │
  │                              │                    │ CLAMP       │ │
  │                              │                    │ [-I_MAX,    │ │
  │                              │                    │  +I_MAX]    │ │
  │                              │                    └─────┬───────┘ │
  │                              │                          │ u_i     │
  │                              v                          v         │
  │                           ┌────────────────────────────────┐      │
  │                           │         ADD: u_p + u_i         │      │
  │                           └───────────────┬────────────────┘      │
  │                                           │ u_raw                  │
  │                                           v                        │
  │                                    ┌─────────────┐                 │
  │                                    │   CLAMP     │                 │
  │                                    │ [-U_MAX,    │                 │
  │                                    │  +U_MAX]    │                 │
  │                                    └──────┬──────┘                 │
  │                                           │                        │
  │                                           v                        │
  │                                        output (Vd or Vq)          │
  │                                                                    │
  │  Anti-windup logic:                                                │
  │    if (u_raw SATURATED) AND (sign(e) == sign(u_raw)):             │
  │      freeze u_i (don't update accumulator)                        │
  └────────────────────────────────────────────────────────────────────┘

  Ports:
    input  en                           // compute enable strobe
    input  [DATA_W-1:0] ref_val         // reference (Id_ref or Iq_ref)
    input  [DATA_W-1:0] meas_val        // measured (Id or Iq)
    input  [DATA_W-1:0] kp              // proportional gain
    input  [DATA_W-1:0] ki              // integral gain (pre-scaled by Ts)
    input  [DATA_W-1:0] out_max         // output clamp limit
    input  [DATA_W-1:0] int_max         // integrator clamp limit
    output [DATA_W-1:0] out_val         // controller output (Vd or Vq)
    output done                         // result valid

  Registers:
    u_i [PI_ACC_W-1:0]                  // integrator state (persistent across iterations)

  Latency: 4 cycles (sub → mul_kp, mul_ki → acc+clamp → add+clamp)
  Resources: 2 multipliers (or 1 shared with 2 extra cycles), 3 adders, 2 comparators
```

### 5.2 Accumulator Width

The integrator register `u_i` uses `PI_ACC_W` bits (default 32) even though the datapath is `DATA_W` bits (default 16). This provides:

```
Extra precision bits: PI_ACC_W - DATA_W = 16 (default)
Extra headroom: prevents overflow for (PI_ACC_W - DATA_W - 1) = 15 bits of accumulation
Maximum steps before overflow (without clamp): 2^15 = 32768
At 20 kHz: 1.6 seconds of constant full-scale error — impossible in practice
```

The output is taken from the upper `DATA_W` bits of `u_i` (right-shift by `PI_ACC_W - DATA_W`), preserving high precision in the low-order bits.

### 5.3 Anti-Windup Implementation

```
// Compute tentative update
delta = ki * e[k]                    // DATA_W × DATA_W → PRODUCT_W, truncate to PI_ACC_W
u_i_tent = u_i + delta

// Integrator clamp
u_i_clamped = clamp(u_i_tent, -int_max_extended, +int_max_extended)

// Check anti-windup condition
u_raw = u_p + u_i_clamped[upper DATA_W bits]
saturated = (u_raw > out_max) || (u_raw < -out_max)
same_sign = (e[k][MSB] == u_raw[MSB])

// Conditional update
if (saturated && same_sign):
    u_i = u_i                        // freeze: don't update
else:
    u_i = u_i_clamped               // normal update
```

## 6. SVPWM Generator Architecture

### 6.1 Block Diagram (Min-Max Method)

```
                        foc_svpwm
  ┌────────────────────────────────────────────────────────────────┐
  │                                                                │
  │  Vα, Vβ ─────────────────────────────────┐                    │
  │                                           │                    │
  │  Stage 1: Inverse Clarke (αβ → abc)       │                    │
  │  ┌──────────────────────────────────────┐ │                    │
  │  │  Va = Vα                             │ │                    │
  │  │  Vb = (-Vα + SQRT3*Vβ) >>> 1        │ │                    │
  │  │  Vc = (-Vα - SQRT3*Vβ) >>> 1        │ │                    │
  │  └────────────────┬─────────────────────┘ │                    │
  │                   │ Va, Vb, Vc            │                    │
  │                   v                       │                    │
  │  Stage 2: Min/Max detection               │                    │
  │  ┌──────────────────────────────────────┐ │                    │
  │  │  Vmax = max(Va, Vb, Vc)             │ │                    │
  │  │  Vmin = min(Va, Vb, Vc)             │ │                    │
  │  │  Voffset = -(Vmax + Vmin) >>> 1     │ │                    │
  │  └────────────────┬─────────────────────┘ │                    │
  │                   │ Voffset               │                    │
  │                   v                       │                    │
  │  Stage 3: Duty cycle computation          │                    │
  │  ┌──────────────────────────────────────┐ │                    │
  │  │  duty_a = HALF + scale(Va + Voff)   │ │                    │
  │  │  duty_b = HALF + scale(Vb + Voff)   │ │                    │
  │  │  duty_c = HALF + scale(Vc + Voff)   │ │                    │
  │  │                                      │ │                    │
  │  │  scale(x) = (x * PWM_MAX) >> FRAC_W │ │                    │
  │  │  Result clamped to [0, PWM_MAX]      │ │                    │
  │  └────────────────┬─────────────────────┘ │                    │
  │                   │                       │                    │
  │                   v                       │                    │
  │             duty_a, duty_b, duty_c [PWM_BITS-1:0]             │
  └────────────────────────────────────────────────────────────────┘

  Ports:
    input  en
    input  [DATA_W-1:0] v_alpha, v_beta
    output [PWM_BITS-1:0] duty_a, duty_b, duty_c
    output done

  Latency: 8 cycles (shared multiplier) or 4 cycles (dedicated)
  Resources: 1 multiplier (SQRT3), 1 multiplier (scale), 6 comparators, 5 adders
```

### 6.2 Min/Max Comparator Tree

```
// 3-input max using 2 comparators
cmp1 = (Va > Vb) ? Va : Vb
Vmax = (cmp1 > Vc) ? cmp1 : Vc

// 3-input min using 2 comparators
cmp2 = (Va < Vb) ? Va : Vb
Vmin = (cmp2 < Vc) ? cmp2 : Vc

// Total: 4 signed comparators (each is a subtractor checking the MSB)
```

### 6.3 Duty Cycle Scaling

The scaling from signed fixed-point voltage to unsigned PWM counter value:

```
// Vx_adj is in signed Q(INT_W).(FRAC_W), range approximately [-0.577, +0.577]
// (After min-max injection, amplitude is bounded by Vdc/sqrt(3))
// Target: unsigned PWM_BITS value, 0 = 0% duty, PWM_MAX = 100% duty

duty = HALF_SCALE + ((Vx_adj * PWM_MAX) >>> FRAC_W)

// Clamp to valid range
if (duty < 0) duty = 0
if (duty > PWM_MAX) duty = PWM_MAX
```

## 7. Control FSM

### 7.1 State Diagram

```
                      ┌─────────┐
             reset───>│  IDLE   │<───────────────────────────────┐
                      └────┬────┘                                │
                           │ CTRL.start = 1                      │
                           v                                     │
                      ┌─────────┐                                │
                      │ SINCOS  │  Compute sin(θ), cos(θ)       │
                      │         │  Wait for sincos.done          │
                      └────┬────┘                                │
                           │ sincos.done                         │
                           v                                     │
                      ┌─────────┐                                │
                      │ CLARKE  │  Iα, Iβ from Ia, Ib           │
                      │         │  2 cycles                      │
                      └────┬────┘                                │
                           │                                     │
                           v                                     │
                      ┌─────────┐                                │
                      │  PARK   │  Id, Iq from Iα, Iβ, sin, cos │
                      │         │  2-6 cycles (config dependent) │
                      └────┬────┘                                │
                           │                                     │
                           v                                     │
                      ┌─────────┐                                │
                      │  PI_D   │  Vd from Id_ref, Id            │
                      │         │  4 cycles                      │
                      └────┬────┘                                │
                           │                                     │
                           v                                     │
                      ┌─────────┐                                │
                      │  PI_Q   │  Vq from Iq_ref, Iq            │
                      │         │  4 cycles                      │
                      └────┬────┘                                │
                           │                                     │
                           v                                     │
                      ┌──────────┐                               │
                      │ INV_PARK │  Vα, Vβ from Vd, Vq, sin, cos│
                      │          │  2-6 cycles                   │
                      └────┬─────┘                               │
                           │                                     │
                           v                                     │
                      ┌─────────┐                                │
                      │  SVPWM  │  duty_a/b/c from Vα, Vβ       │
                      │         │  4-8 cycles                    │
                      └────┬────┘                                │
                           │                                     │
                           v                                     │
                      ┌─────────┐                                │
                      │  DONE   │  Latch outputs, assert IRQ    │
                      │         │  STATUS.done = 1               │
                      └────┬────┘                                │
                           │ CTRL.clear = 1 OR CTRL.start = 1   │
                           └─────────────────────────────────────┘
```

### 7.2 FSM State Encoding

```systemverilog
typedef enum logic [3:0] {
    ST_IDLE     = 4'h0,
    ST_SINCOS   = 4'h1,
    ST_CLARKE   = 4'h2,
    ST_PARK     = 4'h3,
    ST_PI_D     = 4'h4,
    ST_PI_Q     = 4'h5,
    ST_INV_PARK = 4'h6,
    ST_SVPWM    = 4'h7,
    ST_DONE     = 4'h8
} foc_state_t;
```

### 7.3 Continuous Mode

For production use, the coprocessor supports continuous (free-running) mode:

```
CTRL.continuous = 1:
  On DONE, automatically re-read input registers and restart from SINCOS.
  No CPU intervention needed between iterations.
  The CPU updates input registers asynchronously; the coprocessor latches them
  at the IDLE→SINCOS transition.

  Timing: back-to-back iterations separated by 1 idle cycle (for input latching).
```

This enables the coprocessor to run at a fixed rate determined by its computation latency, independent of CPU interrupt response time.

## 8. Register Map

Wishbone B4 slave interface, 32-bit data bus, byte-addressed.

| Offset | Name | Width | R/W | Description |
|--------|------|-------|-----|-------------|
| 0x00 | CTRL | 32 | R/W | Control register |
| | | | | [0] `start`: write 1 to begin FOC iteration |
| | | | | [1] `clear`: write 1 to reset FSM to IDLE, clear PI integrators |
| | | | | [2] `continuous`: 1 = auto-restart after DONE |
| | | | | [3] `pi_reset`: write 1 to zero PI integrators without resetting FSM |
| 0x04 | STATUS | 32 | R | Status register |
| | | | | [0] `busy`: FSM not in IDLE or DONE |
| | | | | [1] `done`: computation complete, results valid |
| | | | | [2] `error`: arithmetic overflow detected |
| | | | | [7:4] `state`: current FSM state (foc_state_t encoding) |
| | | | | [31:16] `cycle_count`: cycles elapsed in current/last iteration |
| 0x08 | IA | 32 | R/W | Phase A current (signed, Q`INT_W`.`FRAC_W` in lower DATA_W bits) |
| 0x0C | IB | 32 | R/W | Phase B current (signed, Q`INT_W`.`FRAC_W` in lower DATA_W bits) |
| 0x10 | THETA | 32 | R/W | Electrical angle (unsigned, ANGLE_W bits) |
| 0x14 | ID_REF | 32 | R/W | D-axis current reference (typically 0 for SPMSM) |
| 0x18 | IQ_REF | 32 | R/W | Q-axis current reference (torque command) |
| 0x1C | KP | 32 | R/W | Proportional gain (signed Q`INT_W`.`FRAC_W`) |
| 0x20 | KI | 32 | R/W | Integral gain, pre-scaled by Ts (signed Q`INT_W`.`FRAC_W`) |
| 0x24 | OUT_MAX | 32 | R/W | PI output clamp magnitude (unsigned, applied as ±) |
| 0x28 | INT_MAX | 32 | R/W | PI integrator clamp magnitude (unsigned, applied as ±) |
| 0x2C | KP_Q | 32 | R/W | Q-axis Kp (if different from d-axis; else alias of KP) |
| 0x30 | KI_Q | 32 | R/W | Q-axis Ki (if different from d-axis; else alias of KI) |
| 0x40 | DUTY_A | 32 | R | Phase A duty cycle (unsigned, PWM_BITS bits) |
| 0x44 | DUTY_B | 32 | R | Phase B duty cycle (unsigned, PWM_BITS bits) |
| 0x48 | DUTY_C | 32 | R | Phase C duty cycle (unsigned, PWM_BITS bits) |
| 0x50 | ID_MEAS | 32 | R | Measured Id (debug readback) |
| 0x54 | IQ_MEAS | 32 | R | Measured Iq (debug readback) |
| 0x58 | IALPHA | 32 | R | Computed Iα (debug readback) |
| 0x5C | IBETA | 32 | R | Computed Iβ (debug readback) |
| 0x60 | VD | 32 | R | PI d-axis output Vd (debug readback) |
| 0x64 | VQ | 32 | R | PI q-axis output Vq (debug readback) |
| 0x68 | VALPHA | 32 | R | Inverse Park Vα (debug readback) |
| 0x6C | VBETA | 32 | R | Inverse Park Vβ (debug readback) |
| 0x70 | PI_D_INT | 32 | R | D-axis PI integrator state (lower DATA_W bits of PI_ACC_W) |
| 0x74 | PI_Q_INT | 32 | R | Q-axis PI integrator state |
| 0x80 | PARAM0 | 32 | R | Design parameters (read-only, hardwired) |
| | | | | [7:0] DATA_W |
| | | | | [15:8] FRAC_W |
| | | | | [23:16] CORDIC_ITERS |
| | | | | [31:24] PWM_BITS |
| 0x84 | PARAM1 | 32 | R | Design parameters (continued) |
| | | | | [7:0] PI_ACC_W |
| | | | | [15:8] ANGLE_W |
| | | | | [16] SINCOS_MODE |
| | | | | [17] SHARED_MUL |
| | | | | [19:18] PIPE_DEPTH |

### Usage Sequence (Firmware Perspective)

```c
// 1. Verify design parameters (optional, for driver compatibility check)
uint32_t p0 = WB_READ(FOC_BASE + 0x80);
assert((p0 & 0xFF) == 16);               // DATA_W = 16
assert(((p0 >> 8) & 0xFF) == 15);        // FRAC_W = 15

// 2. Configure PI gains (once, or when tuning changes)
WB_WRITE(FOC_BASE + 0x1C, kp_q15);       // Kp in Q1.15
WB_WRITE(FOC_BASE + 0x20, ki_q15);       // Ki*Ts in Q1.15
WB_WRITE(FOC_BASE + 0x24, out_max_q15);  // output clamp
WB_WRITE(FOC_BASE + 0x28, int_max_q15);  // integrator clamp

// 3. Configure current references
WB_WRITE(FOC_BASE + 0x14, 0x0000);       // Id_ref = 0 (no field weakening)
WB_WRITE(FOC_BASE + 0x18, iq_ref_q15);   // Iq_ref = torque command

// 4. Each PWM period (called from ADC-done interrupt):
void foc_update(int16_t ia, int16_t ib, uint16_t theta_e) {
    WB_WRITE(FOC_BASE + 0x08, (uint32_t)(int32_t)ia);
    WB_WRITE(FOC_BASE + 0x0C, (uint32_t)(int32_t)ib);
    WB_WRITE(FOC_BASE + 0x10, theta_e);
    WB_WRITE(FOC_BASE + 0x00, 0x01);     // CTRL.start = 1

    // Option A: Poll (for tight loop)
    while (!(WB_READ(FOC_BASE + 0x04) & 0x02));

    // Option B: Wait for IRQ (for interrupt-driven)
    // IRQ fires when STATUS.done = 1

    // Read results (or let external PWM timer read directly)
    duty_a = WB_READ(FOC_BASE + 0x40) & PWM_MAX;
    duty_b = WB_READ(FOC_BASE + 0x44) & PWM_MAX;
    duty_c = WB_READ(FOC_BASE + 0x48) & PWM_MAX;

    // Apply to PWM timer compare registers
    PWM_CMP_A = duty_a;
    PWM_CMP_B = duty_b;
    PWM_CMP_C = duty_c;
}

// 5. Continuous mode (no CPU in the hot loop):
WB_WRITE(FOC_BASE + 0x08, ia);
WB_WRITE(FOC_BASE + 0x0C, ib);
WB_WRITE(FOC_BASE + 0x10, theta_e);
WB_WRITE(FOC_BASE + 0x00, 0x05);         // start + continuous
// Coprocessor now free-runs. CPU updates Ia/Ib/theta asynchronously.
// Duty outputs update every ~47 clock cycles.
```

## 9. Timing and Throughput Analysis

### 9.1 Cycle Count per FOC Iteration

| Configuration | SINCOS | CLARKE | PARK | PI×2 | INV_PARK | SVPWM | TOTAL |
|---------------|--------|--------|------|------|----------|-------|-------|
| CORDIC-16, shared mul | 18 | 2 | 6 | 8 | 6 | 8 | **48** |
| CORDIC-16, dedicated mul | 18 | 2 | 2 | 8 | 2 | 4 | **36** |
| CORDIC-12, shared mul | 14 | 2 | 6 | 8 | 6 | 8 | **44** |
| CORDIC-12, dedicated mul | 14 | 2 | 2 | 8 | 2 | 4 | **32** |
| LUT-1024, shared mul | 3 | 2 | 6 | 8 | 6 | 8 | **33** |
| LUT-1024, dedicated mul | 3 | 2 | 2 | 8 | 2 | 4 | **21** |

Add `PIPE_DEPTH × num_multiplications` for pipelined configurations (approximately 14-18 additional cycles for PIPE_DEPTH=1).

### 9.2 Maximum FOC Update Rate

```
At 50 MHz clock:

Best case (LUT, dedicated, PIPE=0):     21 cycles = 0.42 μs → 2.38 MHz max rate
Typical (CORDIC-16, shared, PIPE=1):    ~62 cycles = 1.24 μs → 806 kHz max rate
Worst case (CORDIC-16, shared, PIPE=2): ~78 cycles = 1.56 μs → 641 kHz max rate

All configurations far exceed the 40 kHz target (25 μs period = 1250 cycles at 50 MHz).
Utilization at 20 kHz: 48/2500 = 1.9% (CORDIC shared) — the coprocessor is idle 98% of the time.
```

### 9.3 Latency Breakdown (Default Config: CORDIC-16, SHARED_MUL=1, PIPE_DEPTH=1)

```
Sensor write to duty output:
  Register write:     1 cycle   (Wishbone ack)
  Start to SINCOS:    1 cycle   (FSM transition)
  SINCOS:            18 cycles  (CORDIC-16 + pre/post)
  CLARKE:             3 cycles  (2 + 1 pipeline)
  PARK:               8 cycles  (6 + 2 pipeline)
  PI_D:               6 cycles  (4 + 2 pipeline)
  PI_Q:               6 cycles  (4 + 2 pipeline)
  INV_PARK:           8 cycles  (6 + 2 pipeline)
  SVPWM:             10 cycles  (8 + 2 pipeline)
  DONE latch:         1 cycle
  ─────────────────────────────
  Total:             62 cycles = 1.24 μs at 50 MHz
```

### 9.4 Comparison to Software

```
PicoRV32 at 50 MHz (software FOC, all in C):
  Clarke:     ~20 cycles (2 muls, adds)
  Sin/cos:    ~200 cycles (CORDIC in software, or LUT+interp)
  Park:       ~40 cycles (4 muls, adds)
  PI × 2:    ~60 cycles (4 muls, adds, branches)
  Inv Park:  ~40 cycles (4 muls, adds)
  SVPWM:     ~80 cycles (muls, comparisons, branches)
  Overhead:  ~60 cycles (function calls, loads/stores)
  ─────────────────────────────
  Total:     ~500 cycles = 10.0 μs at 50 MHz

Hardware speedup: ~500 / 62 ≈ 8×

More importantly: the hardware latency is deterministic and jitter-free,
while software latency varies with cache misses, interrupts, and branch misprediction.
```

## 10. Area Estimates (Pre-Synthesis)

### 10.1 Component Gate Counts

Estimates for Sky130 at DATA_W=16:

| Component | Gate Count | Notes |
|-----------|-----------|-------|
| 16×16 signed multiplier | ~1,200 | Inferred by Yosys; booth/array |
| CORDIC core (iterative, 16 iter) | ~800 | 3 adders + shifter + control |
| ATAN ROM (16 entries × 16 bits) | ~100 | Hardwired constants |
| Clarke datapath | ~400 | 1 mul + 2 adders + shift chain |
| Park datapath (shared mul) | ~300 | Mux into shared mul + 2 adders |
| Park datapath (dedicated, 2 mul) | ~2,700 | 2 muls + 2 adders |
| PI controller (1 instance) | ~2,800 | 2 muls + 3 adders + 32-bit acc + comparators |
| PI controller (shared mul) | ~1,600 | 1 mul + 3 adders + 32-bit acc |
| SVPWM (min-max method) | ~2,000 | 1 mul + 6 comparators + 5 adders |
| Control FSM | ~300 | 9-state FSM + counters |
| Wishbone slave | ~500 | Address decode + register file |
| Register file (inputs + outputs) | ~1,500 | ~30 registers × DATA_W bits |
| Pipeline registers (PIPE=1) | ~400 | ~25 flip-flops × DATA_W |
| Sin/cos LUT (1024×16, 1 quadrant) | ~8,000 | Or SRAM macro (~2 KB) |

### 10.2 Configuration Totals

| Configuration | Multipliers | Est. Gates | Est. Area (Sky130) |
|---------------|-------------|-----------|-------------------|
| CORDIC-16, shared (1 mul) | 1 | ~8,500 | ~0.04 mm² |
| CORDIC-16, dedicated (4 mul) | 4 | ~13,500 | ~0.06 mm² |
| LUT-1024, shared (1 mul) | 1 | ~16,500 | ~0.07 mm² |
| LUT-1024, dedicated (4 mul) | 4 | ~21,500 | ~0.09 mm² |
| Minimal (CORDIC-12, shared, 12-bit) | 1 | ~5,500 | ~0.025 mm² |
| Maximum (LUT, dedicated, 32-bit) | 4 | ~55,000 | ~0.25 mm² |

### 10.3 Multiplier Sharing Analysis

The design has 5 distinct multiply operations per FOC iteration:
1. Clarke: `(Ia + 2*Ib) * INV_SQRT3` — 1 multiply
2. Park: `Iα*cos, Iβ*sin, Iα*sin, Iβ*cos` — 4 multiplies (but only 2 unique operand pairs needed simultaneously)
3. PI_d: `Kp*ed, Ki*ed` — 2 multiplies
4. PI_q: `Kp*eq, Ki*eq` — 2 multiplies
5. Inv Park: same structure as Park — 4 multiplies
6. SVPWM: `SQRT3*Vβ`, 3× `(Vx+Voff)*PWM_MAX` — 4 multiplies

Total: ~17 multiplications per FOC iteration.

With `SHARED_MUL=1` (1 multiplier): 17 multiplications × 1 cycle each = 17 cycles just for multiplies, plus overhead = ~48 total.

With `SHARED_MUL=0` (4 multipliers): parallel multiplies reduce to ~10 multiply-cycles + overhead = ~36 total.

The area-latency tradeoff:
```
1 multiplier:  ~1,200 gates,  48 cycles  → 57,600 gate-cycles
4 multipliers: ~4,800 gates,  36 cycles  → 172,800 gate-cycles (worse efficiency)
2 multipliers: ~2,400 gates,  40 cycles  → 96,000 gate-cycles (sweet spot)
```

The 2-multiplier configuration (one for the CORDIC/transform path, one for PI/SVPWM) may be the Pareto-optimal point — the optimization loop will determine this empirically.

### 10.4 Comparison to Reference Designs

| Design | Technology | Area | Clock | FOC Rate | Notes |
|--------|-----------|------|-------|----------|-------|
| This design (default) | Sky130 | ~0.04 mm² | 50 MHz | 1 MHz+ | Pure digital, no ADC/encoder |
| WangXuan95 FPGA-FOC | Cyclone IV | ~1,500 LEs | 36.9 MHz | 18 kHz | Includes ADC/encoder interfaces |
| TI C2000 (SW) | 65nm MCU | ~2 mm² (full MCU) | 200 MHz | 20 kHz | Software FOC on DSP core |
| ST STSPIN (HW FOC) | 130nm | ~0.5 mm² (FOC block) | 100 MHz | 40 kHz | Mixed-signal with ADC |

The pure-digital coprocessor achieves comparable or better FOC rates in a fraction of the area, since it excludes analog interfaces and general-purpose CPU overhead.
