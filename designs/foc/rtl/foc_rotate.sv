// FOC Motor Coprocessor — Shared Rotation Module (Park + Inverse Park)
//
// Computes both Park (mode=0) and Inverse Park (mode=1) transforms
// using the same 4 multipliers, since they never run concurrently.
//
// Park:     Id = a*cos + b*sin,     Iq = -a*sin + b*cos
// InvPark:  Va = a*cos - b*sin,     Vb =  a*sin + b*cos
//
// Matching reference model: negate-then-clamp before add, saturating final sum.
// 2-cycle latency: cycle 1 = multiply + truncate, cycle 2 = sat-add

module foc_rotate #(
    parameter int DATA_W = 16,
    parameter int FRAC_W = 15
) (
    input  logic                      clk,
    input  logic                      rst_n,
    input  logic                      en,
    input  logic                      mode,  // 0=Park, 1=InvPark
    input  logic signed [DATA_W-1:0]  in_a,     // i_alpha or v_d
    input  logic signed [DATA_W-1:0]  in_b,     // i_beta  or v_q
    input  logic signed [DATA_W-1:0]  sin_val,
    input  logic signed [DATA_W-1:0]  cos_val,
    output logic signed [DATA_W-1:0]  out_x,    // i_d or v_alpha
    output logic signed [DATA_W-1:0]  out_y,    // i_q or v_beta
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
        if (a == NEG_MIN) sat_neg = POS_MAX;
        else sat_neg = -a;
    endfunction

    // ── Full products (same for both modes) ──
    logic signed [2*DATA_W-1:0] p_ac, p_bs, p_as, p_bc;

    // ── Truncated products ──
    logic signed [DATA_W-1:0] t_ac, t_bs, t_as, t_bc;

    always_comb begin
        p_ac = in_a * cos_val;
        p_bs = in_b * sin_val;
        p_as = in_a * sin_val;
        p_bc = in_b * cos_val;

        t_ac = p_ac[FRAC_W +: DATA_W];
        t_bs = p_bs[FRAC_W +: DATA_W];
        t_as = p_as[FRAC_W +: DATA_W];
        t_bc = p_bc[FRAC_W +: DATA_W];
    end

    // ── Pipeline stage 1: truncate + conditional negate ──
    // Park:    out_x = t_ac + t_bs,          out_y = sat_neg(t_as) + t_bc
    // InvPark: out_x = t_ac + sat_neg(t_bs), out_y = t_as + t_bc
    logic signed [DATA_W-1:0] s1_ac, s1_bc, s1_for_x, s1_for_y;
    logic valid_s1, valid_s2;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            s1_ac    <= '0;
            s1_bc    <= '0;
            s1_for_x <= '0;
            s1_for_y <= '0;
            valid_s1 <= 1'b0;
        end else begin
            valid_s1 <= en;
            if (en) begin
                s1_ac <= t_ac;
                s1_bc <= t_bc;
                if (mode) begin
                    // InvPark: negate t_bs for out_x, keep t_as for out_y
                    s1_for_x <= sat_neg(t_bs);
                    s1_for_y <= t_as;
                end else begin
                    // Park: keep t_bs for out_x, negate t_as for out_y
                    s1_for_x <= t_bs;
                    s1_for_y <= sat_neg(t_as);
                end
            end
        end
    end

    // ── Pipeline stage 2: saturating add ──
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            out_x    <= '0;
            out_y    <= '0;
            valid_s2 <= 1'b0;
        end else begin
            valid_s2 <= valid_s1;
            if (valid_s1) begin
                out_x <= sat_add(s1_ac, s1_for_x);
                out_y <= sat_add(s1_for_y, s1_bc);
            end
        end
    end

    assign done = valid_s2;

endmodule
