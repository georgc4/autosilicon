// FOC Motor Coprocessor — Iterative CORDIC Sin/Cos Unit
//
// Computes sin(theta) and cos(theta) via CORDIC rotation mode.
// Quadrant pre-rotation folds any angle to Q1, then post-corrects via negate.
// Per-iteration saturation matches the fixed-point reference model.
// Latency: CORDIC_ITERS + 2 cycles (1 pre-rotate + N iterations + 1 post-correct)

module foc_cordic #(
    parameter int DATA_W       = 16,
    parameter int FRAC_W       = 15,
    parameter int ANGLE_W      = 16,
    parameter int CORDIC_ITERS = 16
) (
    input  logic                    clk,
    input  logic                    rst_n,
    input  logic                    start,
    input  logic [ANGLE_W-1:0]     theta,
    output logic signed [DATA_W-1:0] cos_val,
    output logic signed [DATA_W-1:0] sin_val,
    output logic                    done
);

    import foc_pkg::atan_lut;

    // Compute CORDIC gain locally — package import shadows module params in Icarus
    localparam logic signed [DATA_W-1:0] LOCAL_CORDIC_GAIN =
        (FRAC_W ==  7) ? DATA_W'(78) :
        (FRAC_W ==  8) ? DATA_W'(155) :
        (FRAC_W == 11) ? DATA_W'(1243) :
        (FRAC_W == 12) ? DATA_W'(2487) :
        (FRAC_W == 15) ? DATA_W'(19898) :
        (FRAC_W == 16) ? DATA_W'(39797) :
        (FRAC_W == 23) ? DATA_W'(5093984) :
                         DATA_W'(19898);

    localparam int ITER_W = $clog2(CORDIC_ITERS);
    localparam signed [DATA_W-1:0] POS_MAX = {1'b0, {(DATA_W-1){1'b1}}};
    localparam signed [DATA_W-1:0] NEG_MIN = {1'b1, {(DATA_W-1){1'b0}}};

    // ── Registers (DATA_W-bit, matching reference model) ──
    logic signed [DATA_W-1:0] x_reg, y_reg;
    logic [ANGLE_W-1:0]       z_reg;
    logic [1:0]               quad_reg;
    logic [ITER_W-1:0]        iter;

    typedef enum logic [1:0] {
        S_IDLE    = 2'd0,
        S_ITERATE = 2'd1,
        S_POST    = 2'd2,
        S_DONE    = 2'd3
    } cordic_st_t;

    cordic_st_t cstate;

    // ── Shifted values ──
    logic signed [DATA_W-1:0] x_shift, y_shift;
    logic [ANGLE_W-1:0]       atan_val;
    logic                     d_sign;

    assign x_shift = x_reg >>> iter;
    assign y_shift = y_reg >>> iter;
    assign atan_val = atan_lut(iter[3:0]);
    assign d_sign  = z_reg[ANGLE_W-1];

    // ── Per-iteration update with overflow detection ──
    logic signed [DATA_W:0] x_new, y_new;

    always_comb begin
        if (!d_sign) begin
            x_new = {x_reg[DATA_W-1], x_reg} - {y_shift[DATA_W-1], y_shift};
            y_new = {y_reg[DATA_W-1], y_reg} + {x_shift[DATA_W-1], x_shift};
        end else begin
            x_new = {x_reg[DATA_W-1], x_reg} + {y_shift[DATA_W-1], y_shift};
            y_new = {y_reg[DATA_W-1], y_reg} - {x_shift[DATA_W-1], x_shift};
        end
    end

    // ── Saturate (DATA_W+1) -> DATA_W ──
    function automatic signed [DATA_W-1:0] saturate(input signed [DATA_W:0] val);
        if (val[DATA_W] != val[DATA_W-1])
            saturate = val[DATA_W] ? NEG_MIN : POS_MAX;
        else
            saturate = val[DATA_W-1:0];
    endfunction

    // ── Pre-rotation: fold angle into first quadrant ──
    logic [ANGLE_W-3:0] angle_q1;
    logic [1:0]         quadrant;

    always_comb begin
        quadrant = theta[ANGLE_W-1:ANGLE_W-2];
        angle_q1 = theta[ANGLE_W-3:0];
        if (quadrant[0])
            angle_q1 = ~theta[ANGLE_W-3:0];
    end

    // ── Post-correction: negate based on quadrant ──
    logic negate_cos, negate_sin;
    logic signed [DATA_W-1:0] cos_corrected, sin_corrected;

    assign negate_cos = quad_reg[1] ^ quad_reg[0];
    assign negate_sin = quad_reg[1];

    always_comb begin
        cos_corrected = negate_cos ? -x_reg : x_reg;
        sin_corrected = negate_sin ? -y_reg : y_reg;
    end

    // ── Main FSM ──
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cstate   <= S_IDLE;
            x_reg    <= '0;
            y_reg    <= '0;
            z_reg    <= '0;
            quad_reg <= '0;
            iter     <= '0;
            cos_val  <= '0;
            sin_val  <= '0;
            done     <= 1'b0;
        end else begin
            done <= 1'b0;

            case (cstate)
                S_IDLE: begin
                    if (start) begin
                        x_reg    <= LOCAL_CORDIC_GAIN;
                        y_reg    <= '0;
                        z_reg    <= {{2{1'b0}}, angle_q1};
                        quad_reg <= quadrant;
                        iter     <= '0;
                        cstate   <= S_ITERATE;
                    end
                end

                S_ITERATE: begin
                    x_reg <= saturate(x_new);
                    y_reg <= saturate(y_new);

                    if (!d_sign)
                        z_reg <= z_reg - atan_val;
                    else
                        z_reg <= z_reg + atan_val;

                    if (iter == ITER_W'(CORDIC_ITERS - 1)) begin
                        cstate <= S_POST;
                    end
                    iter <= iter + 1;
                end

                S_POST: begin
                    cos_val <= cos_corrected;
                    sin_val <= sin_corrected;
                    done    <= 1'b1;
                    cstate  <= S_DONE;
                end

                S_DONE: begin
                    cstate <= S_IDLE;
                end

                default: cstate <= S_IDLE;
            endcase
        end
    end

endmodule
