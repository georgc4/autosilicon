// FOC Motor Coprocessor — Parameter Package
// Defines all design parameters, derived constants, types, and CORDIC tables.

package foc_pkg;

  // ── Datapath Parameters ────────────────────────────────────────
  parameter int DATA_W        = 16;    // Fixed-point word width (Q1.15 default)
  parameter int FRAC_W        = 15;    // Fractional bits
  parameter int CORDIC_ITERS  = 16;    // CORDIC iterations (1 bit precision each)
  parameter int PIPE_DEPTH    = 1;     // Pipeline register depth
  parameter int ANGLE_W       = 16;    // Angle representation width
  parameter int PI_ACC_W      = 2 * DATA_W;  // PI integrator accumulator width (must be >= DATA_W + FRAC_W)
  parameter int SINCOS_MODE   = 0;     // 0=CORDIC, 1=LUT
  parameter int PWM_BITS      = 10;    // PWM duty cycle resolution
  parameter int SHARED_MUL    = 0;     // 0=dedicated, 1=shared multipliers
  parameter int ROUND_MODE    = 0;     // 0=truncation, 1=convergent rounding

  // ── Derived Parameters ─────────────────────────────────────────
  localparam int INT_W        = DATA_W - FRAC_W;
  localparam int PRODUCT_W    = 2 * DATA_W;
  localparam int PI_FRAC_W    = PI_ACC_W - INT_W;
  localparam int PWM_MAX      = (1 << PWM_BITS) - 1;
  localparam int HALF_SCALE   = 1 << (PWM_BITS - 1);

  // ── Fixed-Point Constants (Q1.15) ─────────────────────────────
  localparam logic signed [DATA_W-1:0] CORDIC_GAIN = 16'h4DBA;  // 1/K ≈ 0.607253
  localparam logic signed [DATA_W-1:0] INV_SQRT3   = 16'h49E7;  // 1/sqrt(3) ≈ 0.57735
  localparam int                       SQRT3_INT   = 56756;      // sqrt(3) in Q1.15 ≈ 1.73205

  // ── FSM State Encoding ─────────────────────────────────────────
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

  // ── CORDIC Arctangent Table (16 entries, ANGLE_W=16) ──────────
  // atan(2^-i) scaled to 16-bit angle: round(atan(2^-i) / 2π * 65536)
  function automatic [ANGLE_W-1:0] atan_lut(input [3:0] i);
    case (i)
      4'd0:    atan_lut = 16'd8192;
      4'd1:    atan_lut = 16'd4836;
      4'd2:    atan_lut = 16'd2555;
      4'd3:    atan_lut = 16'd1297;
      4'd4:    atan_lut = 16'd651;
      4'd5:    atan_lut = 16'd326;
      4'd6:    atan_lut = 16'd163;
      4'd7:    atan_lut = 16'd81;
      4'd8:    atan_lut = 16'd41;
      4'd9:    atan_lut = 16'd20;
      4'd10:   atan_lut = 16'd10;
      4'd11:   atan_lut = 16'd5;
      4'd12:   atan_lut = 16'd3;
      4'd13:   atan_lut = 16'd1;
      4'd14:   atan_lut = 16'd1;
      4'd15:   atan_lut = 16'd0;
      default: atan_lut = 16'd0;
    endcase
  endfunction

endpackage
