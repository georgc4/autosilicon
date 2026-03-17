// FOC Motor Coprocessor — SVPWM Generator (Min-Max Method)
//
// Inverse Clarke -> min/max -> third-harmonic injection -> duty scaling
// Uses shift-add chain for sqrt(3) multiply (matching reference model).
//
// 3-cycle latency

module foc_svpwm #(
    parameter int DATA_W   = 16,
    parameter int FRAC_W   = 15,
    parameter int PWM_BITS = 10
) (
    input  logic                      clk,
    input  logic                      rst_n,
    input  logic                      en,
    input  logic signed [DATA_W-1:0]  v_alpha,
    input  logic signed [DATA_W-1:0]  v_beta,
    output logic [PWM_BITS-1:0]       duty_a,
    output logic [PWM_BITS-1:0]       duty_b,
    output logic [PWM_BITS-1:0]       duty_c,
    output logic                      done
);

    import foc_pkg::*;

    localparam int PWM_MAX_L    = (1 << PWM_BITS) - 1;
    localparam int HALF_SCALE_L = 1 << (PWM_BITS - 1);
    localparam signed [DATA_W-1:0] POS_MAX = {1'b0, {(DATA_W-1){1'b1}}};
    localparam signed [DATA_W-1:0] NEG_MIN = {1'b1, {(DATA_W-1){1'b0}}};

    // ── sqrt(3) * x via shift-add chain (matching Python model) ──
    // sqrt(3) ≈ 1 + 1/2 + 1/8 + 1/16 + 1/32 + 1/128 + 1/256 + 1/1024 + 1/2048
    //         = 1.732421875
    function automatic signed [DATA_W-1:0] sqrt3_mul(input signed [DATA_W-1:0] x);
        logic signed [DATA_W+1:0] xe;
        logic signed [DATA_W+1:0] result;
        xe = $signed({{2{x[DATA_W-1]}}, x});
        result = xe + (xe >>> 1) + (xe >>> 3) + (xe >>> 4) + (xe >>> 5)
               + (xe >>> 7) + (xe >>> 8) + (xe >>> 10) + (xe >>> 11);
        // Clamp to DATA_W
        if (result > $signed({{2{1'b0}}, POS_MAX}))
            sqrt3_mul = POS_MAX;
        else if (result < $signed({{2{1'b1}}, NEG_MIN}))
            sqrt3_mul = NEG_MIN;
        else
            sqrt3_mul = result[DATA_W-1:0];
    endfunction

    // ── Stage 1: Inverse Clarke (αβ → abc) ──
    // Intermediate sums need DATA_W+1 bits to avoid overflow before >>> 1
    logic signed [DATA_W-1:0] sqrt3_vb;
    logic signed [DATA_W:0]   neg_va_ext, sqrt3_vb_ext;
    logic signed [DATA_W:0]   vb_wide, vc_wide;
    logic signed [DATA_W-1:0] vb_s1, vc_s1;
    logic                     valid_s1;

    always_comb begin
        sqrt3_vb     = sqrt3_mul(v_beta);
        neg_va_ext   = -{v_alpha[DATA_W-1], v_alpha};
        sqrt3_vb_ext = {sqrt3_vb[DATA_W-1], sqrt3_vb};
        vb_wide      = (neg_va_ext + sqrt3_vb_ext) >>> 1;
        vc_wide      = (neg_va_ext - sqrt3_vb_ext) >>> 1;
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            vb_s1    <= '0;
            vc_s1    <= '0;
            valid_s1 <= 1'b0;
        end else begin
            valid_s1 <= en;
            if (en) begin
                vb_s1 <= vb_wide[DATA_W-1:0];
                vc_s1 <= vc_wide[DATA_W-1:0];
            end
        end
    end

    // ── Stage 2: Min/Max and offset ──
    logic signed [DATA_W-1:0] vmax, vmin, voffset;
    logic signed [DATA_W-1:0] va_s2, vb_s2, vc_s2;
    logic                     valid_s2;

    always_comb begin
        // 3-input max
        if (v_alpha >= vb_s1 && v_alpha >= vc_s1)
            vmax = v_alpha;
        else if (vb_s1 >= vc_s1)
            vmax = vb_s1;
        else
            vmax = vc_s1;

        // 3-input min
        if (v_alpha <= vb_s1 && v_alpha <= vc_s1)
            vmin = v_alpha;
        else if (vb_s1 <= vc_s1)
            vmin = vb_s1;
        else
            vmin = vc_s1;

        voffset = -(vmax + vmin) >>> 1;
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            va_s2    <= '0;
            vb_s2    <= '0;
            vc_s2    <= '0;
            valid_s2 <= 1'b0;
        end else begin
            valid_s2 <= valid_s1;
            if (valid_s1) begin
                va_s2 <= v_alpha + voffset;
                vb_s2 <= vb_s1 + voffset;
                vc_s2 <= vc_s1 + voffset;
            end
        end
    end

    // ── Stage 3: Scale to PWM range ──
    // duty = half + (vx_adj * pwm_max) >> FRAC_W
    logic signed [DATA_W+PWM_BITS-1:0] scale_a, scale_b, scale_c;
    logic signed [PWM_BITS+1:0]        duty_a_raw, duty_b_raw, duty_c_raw;
    logic                              valid_s3;

    always_comb begin
        scale_a = va_s2 * $signed((PWM_BITS+1)'(PWM_MAX_L));
        scale_b = vb_s2 * $signed((PWM_BITS+1)'(PWM_MAX_L));
        scale_c = vc_s2 * $signed((PWM_BITS+1)'(PWM_MAX_L));

        duty_a_raw = $signed((PWM_BITS+2)'(HALF_SCALE_L)) + (PWM_BITS+2)'(scale_a >>> FRAC_W);
        duty_b_raw = $signed((PWM_BITS+2)'(HALF_SCALE_L)) + (PWM_BITS+2)'(scale_b >>> FRAC_W);
        duty_c_raw = $signed((PWM_BITS+2)'(HALF_SCALE_L)) + (PWM_BITS+2)'(scale_c >>> FRAC_W);
    end

    // Clamp to [0, PWM_MAX]
    function automatic [PWM_BITS-1:0] clamp_duty(input signed [PWM_BITS+1:0] val);
        if (val < 0)
            clamp_duty = '0;
        else if (val > $signed((PWM_BITS+2)'(PWM_MAX_L)))
            clamp_duty = PWM_BITS'(PWM_MAX_L);
        else
            clamp_duty = val[PWM_BITS-1:0];
    endfunction

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            duty_a   <= '0;
            duty_b   <= '0;
            duty_c   <= '0;
            valid_s3 <= 1'b0;
        end else begin
            valid_s3 <= valid_s2;
            if (valid_s2) begin
                duty_a <= clamp_duty(duty_a_raw);
                duty_b <= clamp_duty(duty_b_raw);
                duty_c <= clamp_duty(duty_c_raw);
            end
        end
    end

    assign done = valid_s3;

endmodule
