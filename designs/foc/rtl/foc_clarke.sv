// FOC Motor Coprocessor — Clarke Transform (abc -> αβ)
//
// Ialpha = Ia
// Ibeta  = (Ia + 2*Ib) * INV_SQRT3
//
// Intermediate additions are saturating DATA_W-bit (matching reference model).
// 2-cycle latency: cycle 1 = sat-add, cycle 2 = multiply + truncate

module foc_clarke #(
    parameter int DATA_W  = 16,
    parameter int FRAC_W  = 15
) (
    input  logic                      clk,
    input  logic                      rst_n,
    input  logic                      en,
    input  logic signed [DATA_W-1:0]  ia,
    input  logic signed [DATA_W-1:0]  ib,
    output logic signed [DATA_W-1:0]  i_alpha,
    output logic signed [DATA_W-1:0]  i_beta,
    output logic                      done
);

    import foc_pkg::*;

    localparam signed [DATA_W-1:0] POS_MAX = {1'b0, {(DATA_W-1){1'b1}}};
    localparam signed [DATA_W-1:0] NEG_MIN = {1'b1, {(DATA_W-1){1'b0}}};

    // ── Saturating add ──
    function automatic signed [DATA_W-1:0] sat_add(
        input signed [DATA_W-1:0] a,
        input signed [DATA_W-1:0] b
    );
        logic signed [DATA_W:0] sum;
        sum = {a[DATA_W-1], a} + {b[DATA_W-1], b};
        if (sum > $signed({1'b0, POS_MAX}))
            sat_add = POS_MAX;
        else if (sum < $signed({1'b1, NEG_MIN}))
            sat_add = NEG_MIN;
        else
            sat_add = sum[DATA_W-1:0];
    endfunction

    // ── Pipeline registers ──
    logic signed [DATA_W-1:0] sum_stage;  // sat(ia + sat(2*ib))
    logic                     valid_s1, valid_s2;

    // Stage 1: compute sat(ia + sat(ib + ib))
    logic signed [DATA_W-1:0] ib_2;

    always_comb begin
        ib_2 = sat_add(ib, ib);
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sum_stage <= '0;
            valid_s1  <= 1'b0;
        end else begin
            valid_s1 <= en;
            if (en) begin
                sum_stage <= sat_add(ia, ib_2);
            end
        end
    end

    // Stage 2: multiply sum by INV_SQRT3, truncate
    logic signed [2*DATA_W-1:0] product;

    always_comb begin
        product = sum_stage * $signed(INV_SQRT3);
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            i_alpha  <= '0;
            i_beta   <= '0;
            valid_s2 <= 1'b0;
        end else begin
            valid_s2 <= valid_s1;
            if (valid_s1) begin
                i_alpha <= ia;
                i_beta  <= product[FRAC_W +: DATA_W];
            end
        end
    end

    assign done = valid_s2;

endmodule
