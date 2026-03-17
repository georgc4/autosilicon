// FOC Motor Coprocessor — PI Controller with Anti-Windup
//
// Accumulator stores raw ki*e products (2*DATA_W-bit, clamped to PI_ACC_W).
// Output extracted by >>> FRAC_W to get DATA_W-bit Q(INT_W).(FRAC_W).
// Anti-windup: freeze integrator when output saturated AND error same sign.
//
// PI_ACC_W must be >= DATA_W + FRAC_W for correct extraction.
// Default: 2*DATA_W (scales automatically with datapath width).
//
// 4-cycle latency:
//   Cycle 1: error = sat(ref - meas)
//   Cycle 2: u_p = fixed_mul(kp, e), delta = ki * e (raw product)
//   Cycle 3: accumulate + clamp integrator
//   Cycle 4: output sum + clamp + anti-windup decision

module foc_pi #(
    parameter int DATA_W   = 16,
    parameter int FRAC_W   = 15,
    parameter int PI_ACC_W = 2 * DATA_W
) (
    input  logic                      clk,
    input  logic                      rst_n,
    input  logic                      en,
    input  logic                      clear,
    input  logic signed [DATA_W-1:0]  ref_val,
    input  logic signed [DATA_W-1:0]  meas_val,
    input  logic signed [DATA_W-1:0]  kp,
    input  logic signed [DATA_W-1:0]  ki,
    input  logic signed [DATA_W-1:0]  out_max,
    input  logic signed [DATA_W-1:0]  int_max,
    output logic signed [DATA_W-1:0]  out_val,
    output logic                      done
);

    import foc_pkg::*;

    localparam signed [DATA_W-1:0]    POS_MAX = {1'b0, {(DATA_W-1){1'b1}}};
    localparam signed [DATA_W-1:0]    NEG_MIN = {1'b1, {(DATA_W-1){1'b0}}};
    localparam signed [PI_ACC_W-1:0]  ACC_MAX = {1'b0, {(PI_ACC_W-1){1'b1}}};
    localparam signed [PI_ACC_W-1:0]  ACC_MIN = {1'b1, {(PI_ACC_W-1){1'b0}}};
    localparam int PI_PROD_W = 2 * DATA_W;

    // ── Integrator state (persistent across FOC iterations) ──
    logic signed [PI_ACC_W-1:0] u_i;

    // ── Pipeline signals ──
    logic signed [DATA_W-1:0]    error_s1;
    logic                        valid_s1, valid_s2, valid_s3, valid_s4;

    logic signed [DATA_W-1:0]    u_p_s2;
    logic signed [PI_ACC_W-1:0]  delta_s2;
    logic                        error_sign_s2;

    logic signed [PI_ACC_W-1:0]  u_i_clamped_s3;
    logic signed [DATA_W-1:0]    u_p_s3;
    logic                        error_sign_s3;

    // ── int_max extended to accumulator scale ──
    // int_max is Q(INT_W).(FRAC_W); shift left by FRAC_W to align with accumulator
    logic signed [PI_ACC_W-1:0] int_max_ext;
    assign int_max_ext = PI_ACC_W'($signed(int_max)) <<< FRAC_W;

    // ── Clamp helpers ──
    function automatic signed [DATA_W-1:0] clamp_dw(input signed [DATA_W:0] val);
        if (val > $signed({1'b0, POS_MAX})) clamp_dw = POS_MAX;
        else if (val < $signed({1'b1, NEG_MIN})) clamp_dw = NEG_MIN;
        else clamp_dw = val[DATA_W-1:0];
    endfunction

    function automatic signed [PI_ACC_W-1:0] clamp_acc(
        input signed [PI_ACC_W:0] val
    );
        if (val > $signed({1'b0, ACC_MAX})) clamp_acc = ACC_MAX;
        else if (val < $signed({1'b1, ACC_MIN})) clamp_acc = ACC_MIN;
        else clamp_acc = val[PI_ACC_W-1:0];
    endfunction

    // ── Stage 1: Compute error with saturation ──
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            error_s1 <= '0;
            valid_s1 <= 1'b0;
        end else begin
            valid_s1 <= en;
            if (en) begin
                error_s1 <= clamp_dw(
                    {ref_val[DATA_W-1], ref_val} - {meas_val[DATA_W-1], meas_val}
                );
            end
        end
    end

    // ── Stage 2: Kp * e (truncated to DATA_W) and ki * e (raw product) ──
    logic signed [PI_PROD_W-1:0] kp_e_full, ki_e_full;

    always_comb begin
        kp_e_full = kp * error_s1;
        ki_e_full = ki * error_s1;
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            u_p_s2   <= '0;
            delta_s2 <= '0;
            error_sign_s2 <= 1'b0;
            valid_s2 <= 1'b0;
        end else begin
            valid_s2 <= valid_s1;
            if (valid_s1) begin
                u_p_s2   <= kp_e_full[FRAC_W +: DATA_W];
                // Sign-extend or truncate raw product to PI_ACC_W
                delta_s2 <= PI_ACC_W'($signed(ki_e_full));
                error_sign_s2 <= error_s1[DATA_W-1];
            end
        end
    end

    // ── Stage 3: Accumulate and clamp integrator ──
    logic signed [PI_ACC_W:0]   u_i_tent_wide;
    logic signed [PI_ACC_W-1:0] u_i_tent, u_i_int_clamped;

    always_comb begin
        u_i_tent_wide = {u_i[PI_ACC_W-1], u_i} + {delta_s2[PI_ACC_W-1], delta_s2};
        u_i_tent = clamp_acc(u_i_tent_wide);
        // Integrator magnitude clamp
        if (u_i_tent > int_max_ext)
            u_i_int_clamped = int_max_ext;
        else if (u_i_tent < -int_max_ext)
            u_i_int_clamped = -int_max_ext;
        else
            u_i_int_clamped = u_i_tent;
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            u_p_s3   <= '0;
            error_sign_s3 <= 1'b0;
            valid_s3 <= 1'b0;
        end else begin
            valid_s3 <= valid_s2;
            if (valid_s2) begin
                u_p_s3   <= u_p_s2;
                error_sign_s3 <= error_sign_s2;
            end
        end
    end

    // ── Stage 4: Output sum, clamp, anti-windup ──
    logic signed [PI_ACC_W-1:0] u_i_shifted;
    logic signed [DATA_W-1:0]   u_i_out;
    logic signed [DATA_W:0]     u_raw;
    logic                       saturated, same_sign;

    always_comb begin
        // Extract DATA_W-bit value from accumulator by >>> FRAC_W
        u_i_shifted = u_i_int_clamped >>> FRAC_W;
        u_i_out     = u_i_shifted[DATA_W-1:0];
        u_raw       = {u_p_s3[DATA_W-1], u_p_s3} + {u_i_out[DATA_W-1], u_i_out};
        saturated   = (u_raw > $signed({1'b0, out_max})) ||
                      (u_raw < -$signed({1'b0, out_max}));
        same_sign   = (error_sign_s3 == u_raw[DATA_W]);
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            u_i     <= '0;
            out_val <= '0;
            valid_s4<= 1'b0;
        end else if (clear) begin
            u_i     <= '0;
            out_val <= '0;
            valid_s4<= 1'b0;
        end else begin
            valid_s4 <= valid_s3;
            if (valid_s3) begin
                // Anti-windup
                if (saturated && same_sign)
                    u_i <= u_i;
                else
                    u_i <= u_i_int_clamped;

                // Output clamp
                if (u_raw > $signed({1'b0, out_max}))
                    out_val <= out_max;
                else if (u_raw < -$signed({1'b0, out_max}))
                    out_val <= -out_max;
                else
                    out_val <= u_raw[DATA_W-1:0];
            end
        end
    end

    assign done = valid_s4;

endmodule
