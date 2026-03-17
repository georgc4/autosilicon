// FOC Motor Coprocessor — Park Transform (αβ -> dq)
//
// Id =  Ialpha * cos(θ) + Ibeta * sin(θ)
// Iq = -Ialpha * sin(θ) + Ibeta * cos(θ)
//
// Matching reference model: negate-then-clamp before add, saturating final sum.
// 2-cycle latency: cycle 1 = multiply + truncate, cycle 2 = sat-add

module foc_park #(
    parameter int DATA_W     = 16,
    parameter int FRAC_W     = 15,
    parameter int SHARED_MUL = 0
) (
    input  logic                      clk,
    input  logic                      rst_n,
    input  logic                      en,
    input  logic signed [DATA_W-1:0]  i_alpha,
    input  logic signed [DATA_W-1:0]  i_beta,
    input  logic signed [DATA_W-1:0]  sin_val,
    input  logic signed [DATA_W-1:0]  cos_val,
    output logic signed [DATA_W-1:0]  i_d,
    output logic signed [DATA_W-1:0]  i_q,
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
        if (s[DATA_W] != s[DATA_W-1]) sat_add = s[DATA_W] ? NEG_MIN : POS_MAX;
        else sat_add = s[DATA_W-1:0];
    endfunction

    function automatic signed [DATA_W-1:0] sat_neg(input signed [DATA_W-1:0] a);
        if (a == NEG_MIN) sat_neg = POS_MAX;  // -(-1.0) saturates to +max
        else sat_neg = -a;
    endfunction

    // ── Full products ──
    logic signed [2*DATA_W-1:0] p_ac, p_bs, p_as, p_bc;

    // ── Pipeline stage 1: multiply + truncate ──
    logic signed [DATA_W-1:0] t_ac, t_bs, t_neg_as, t_bc;
    logic valid_s1, valid_s2;

    always_comb begin
        p_ac = i_alpha * cos_val;
        p_bs = i_beta  * sin_val;
        p_as = i_alpha * sin_val;
        p_bc = i_beta  * cos_val;
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            t_ac     <= '0;
            t_bs     <= '0;
            t_neg_as <= '0;
            t_bc     <= '0;
            valid_s1 <= 1'b0;
        end else begin
            valid_s1 <= en;
            if (en) begin
                t_ac     <= p_ac[FRAC_W +: DATA_W];
                t_bs     <= p_bs[FRAC_W +: DATA_W];
                t_neg_as <= sat_neg(p_as[FRAC_W +: DATA_W]);
                t_bc     <= p_bc[FRAC_W +: DATA_W];
            end
        end
    end

    // ── Pipeline stage 2: saturating add ──
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            i_d      <= '0;
            i_q      <= '0;
            valid_s2 <= 1'b0;
        end else begin
            valid_s2 <= valid_s1;
            if (valid_s1) begin
                i_d <= sat_add(t_ac, t_bs);
                i_q <= sat_add(t_neg_as, t_bc);
            end
        end
    end

    assign done = valid_s2;

endmodule
