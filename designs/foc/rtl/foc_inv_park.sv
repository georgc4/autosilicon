// FOC Motor Coprocessor — Inverse Park Transform (dq -> αβ)
//
// Valpha =  Vd * cos(θ) - Vq * sin(θ)
// Vbeta  =  Vd * sin(θ) + Vq * cos(θ)
//
// Matching reference model: negate-then-clamp before add, saturating final sum.
// 2-cycle latency: cycle 1 = multiply + truncate, cycle 2 = sat-add

module foc_inv_park #(
    parameter int DATA_W     = 16,
    parameter int FRAC_W     = 15,
    parameter int SHARED_MUL = 0
) (
    input  logic                      clk,
    input  logic                      rst_n,
    input  logic                      en,
    input  logic signed [DATA_W-1:0]  v_d,
    input  logic signed [DATA_W-1:0]  v_q,
    input  logic signed [DATA_W-1:0]  sin_val,
    input  logic signed [DATA_W-1:0]  cos_val,
    output logic signed [DATA_W-1:0]  v_alpha,
    output logic signed [DATA_W-1:0]  v_beta,
    output logic                      done
);

    import foc_pkg::*;

    localparam signed [DATA_W-1:0] POS_MAX = {1'b0, {(DATA_W-1){1'b1}}};
    localparam signed [DATA_W-1:0] NEG_MIN = {1'b1, {(DATA_W-1){1'b0}}};

    function automatic signed [DATA_W-1:0] sat_add(
        input signed [DATA_W-1:0] a, input signed [DATA_W-1:0] b
    );
        logic signed [DATA_W:0] s;
        s = {a[DATA_W-1], a} + {b[DATA_W-1], b};
        if (s > $signed({1'b0, POS_MAX})) sat_add = POS_MAX;
        else if (s < $signed({1'b1, NEG_MIN})) sat_add = NEG_MIN;
        else sat_add = s[DATA_W-1:0];
    endfunction

    function automatic signed [DATA_W-1:0] sat_neg(input signed [DATA_W-1:0] a);
        if (a == NEG_MIN) sat_neg = POS_MAX;
        else sat_neg = -a;
    endfunction

    // ── Full products ──
    logic signed [2*DATA_W-1:0] p_dc, p_qs, p_ds, p_qc;

    // ── Pipeline stage 1: multiply + truncate ──
    logic signed [DATA_W-1:0] t_dc, t_neg_qs, t_ds, t_qc;
    logic valid_s1, valid_s2;

    always_comb begin
        p_dc = v_d * cos_val;
        p_qs = v_q * sin_val;
        p_ds = v_d * sin_val;
        p_qc = v_q * cos_val;
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            t_dc     <= '0;
            t_neg_qs <= '0;
            t_ds     <= '0;
            t_qc     <= '0;
            valid_s1 <= 1'b0;
        end else begin
            valid_s1 <= en;
            if (en) begin
                t_dc     <= p_dc[FRAC_W +: DATA_W];
                t_neg_qs <= sat_neg(p_qs[FRAC_W +: DATA_W]);
                t_ds     <= p_ds[FRAC_W +: DATA_W];
                t_qc     <= p_qc[FRAC_W +: DATA_W];
            end
        end
    end

    // ── Pipeline stage 2: saturating add ──
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            v_alpha  <= '0;
            v_beta   <= '0;
            valid_s2 <= 1'b0;
        end else begin
            valid_s2 <= valid_s1;
            if (valid_s1) begin
                v_alpha <= sat_add(t_dc, t_neg_qs);
                v_beta  <= sat_add(t_ds, t_qc);
            end
        end
    end

    assign done = valid_s2;

endmodule
