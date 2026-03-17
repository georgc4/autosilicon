// FOC Motor Coprocessor — Top Level
//
// Integrates all datapath modules with a Wishbone B4 slave interface
// and 9-state FSM controller. Register map per microarchitecture spec section 8.

module foc_top #(
    parameter int DATA_W       = 16,
    parameter int FRAC_W       = 15,
    parameter int CORDIC_ITERS = 16,
    parameter int PIPE_DEPTH   = 1,
    parameter int ANGLE_W      = 16,
    parameter int PI_ACC_W     = 2 * DATA_W,
    parameter int SINCOS_MODE  = 0,
    parameter int PWM_BITS     = 10,
    parameter int SHARED_MUL   = 0,
    parameter int ROUND_MODE   = 0
) (
    input  logic        clk,
    input  logic        rst_n,

    // ── Wishbone B4 Slave Interface ──
    input  logic        wb_cyc_i,
    input  logic        wb_stb_i,
    input  logic        wb_we_i,
    input  logic [7:0]  wb_adr_i,
    input  logic [31:0] wb_dat_i,
    output logic [31:0] wb_dat_o,
    output logic        wb_ack_o,

    // ── Outputs ──
    output logic                  irq,
    output logic [PWM_BITS-1:0]  duty_a,
    output logic [PWM_BITS-1:0]  duty_b,
    output logic [PWM_BITS-1:0]  duty_c
);

    import foc_pkg::*;

    // ════════════════════════════════════════════════════════════════
    // Register file
    // ════════════════════════════════════════════════════════════════

    // Control / status
    logic        reg_start, reg_clear, reg_continuous, reg_pi_reset;
    logic        status_busy, status_done, status_error;
    logic [15:0] cycle_count;

    // Input registers
    logic signed [DATA_W-1:0]  reg_ia, reg_ib;
    logic [ANGLE_W-1:0]        reg_theta;
    logic signed [DATA_W-1:0]  reg_id_ref, reg_iq_ref;
    logic signed [DATA_W-1:0]  reg_kp, reg_ki;
    logic signed [DATA_W-1:0]  reg_out_max, reg_int_max;
    logic signed [DATA_W-1:0]  reg_kp_q, reg_ki_q;

    // PI integrator debug (direct hierarchy access)
    logic signed [PI_ACC_W-1:0] dbg_pi_d_int, dbg_pi_q_int;

    // Output registers (removed — SVPWM outputs are already registered)

    // ════════════════════════════════════════════════════════════════
    // FSM
    // ════════════════════════════════════════════════════════════════

    foc_state_t state, state_next;

    // Submodule handshake signals
    logic cordic_start, cordic_done;
    logic clarke_en,    clarke_done;
    logic park_en,      park_done;
    logic pi_d_en,      pi_d_done;
    logic pi_q_en,      pi_q_done;
    logic inv_park_en,  inv_park_done;
    logic svpwm_en,     svpwm_done;

    // ── Datapath wires ──
    logic signed [DATA_W-1:0] w_ialpha, w_ibeta;
    logic signed [DATA_W-1:0] w_id, w_iq;
    logic signed [DATA_W-1:0] w_vd, w_vq;
    logic signed [DATA_W-1:0] w_valpha, w_vbeta;
    logic signed [DATA_W-1:0] w_cos, w_sin;
    logic [PWM_BITS-1:0]      w_duty_a, w_duty_b, w_duty_c;

    // ── Cycle counter ──
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            cycle_count <= '0;
        else if (state == ST_IDLE)
            cycle_count <= '0;
        else if (!cycle_count[15])  // saturate at max
            cycle_count <= cycle_count + 1;
    end

    // ── FSM transition ──
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            state <= ST_IDLE;
        else if (reg_clear)
            state <= ST_IDLE;
        else
            state <= state_next;
    end

    always_comb begin
        state_next    = state;
        cordic_start  = 1'b0;
        clarke_en     = 1'b0;
        park_en       = 1'b0;
        pi_d_en       = 1'b0;
        pi_q_en       = 1'b0;
        inv_park_en   = 1'b0;
        svpwm_en      = 1'b0;

        case (state)
            ST_IDLE: begin
                if (reg_start) begin
                    state_next   = ST_SINCOS;
                    cordic_start = 1'b1;
                end
            end

            ST_SINCOS: begin
                if (cordic_done) begin
                    state_next = ST_CLARKE;
                    clarke_en  = 1'b1;
                end
            end

            ST_CLARKE: begin
                if (clarke_done) begin
                    state_next = ST_PARK;
                    park_en    = 1'b1;
                end
            end

            ST_PARK: begin
                if (park_done) begin
                    state_next = ST_PI_D;
                    pi_d_en    = 1'b1;
                end
            end

            ST_PI_D: begin
                if (pi_d_done) begin
                    state_next = ST_PI_Q;
                    pi_q_en    = 1'b1;
                end
            end

            ST_PI_Q: begin
                if (pi_q_done) begin
                    state_next  = ST_INV_PARK;
                    inv_park_en = 1'b1;
                end
            end

            ST_INV_PARK: begin
                if (inv_park_done) begin
                    state_next = ST_SVPWM;
                    svpwm_en   = 1'b1;
                end
            end

            ST_SVPWM: begin
                if (svpwm_done) begin
                    state_next = ST_DONE;
                end
            end

            ST_DONE: begin
                if (reg_continuous || reg_start) begin
                    state_next   = ST_SINCOS;
                    cordic_start = 1'b1;
                end else if (reg_clear) begin
                    state_next = ST_IDLE;
                end
            end

            default: state_next = ST_IDLE;
        endcase
    end

    // ── Status signals ──
    assign status_busy = (state != ST_IDLE) && (state != ST_DONE);
    assign status_error = 1'b0;  // reserved for future overflow detection

    assign status_done = (state == ST_DONE);

    // Duty outputs read directly from SVPWM (already registered in submodule)
    assign duty_a = w_duty_a;
    assign duty_b = w_duty_b;
    assign duty_c = w_duty_c;

    // ── IRQ: pulse on entering DONE ──
    logic state_was_svpwm;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            irq <= 1'b0;
            state_was_svpwm <= 1'b0;
        end else begin
            state_was_svpwm <= (state == ST_SVPWM);
            irq <= state_was_svpwm && (state == ST_DONE);
        end
    end

    // Debug readback: submodule outputs are registered and hold between iterations,
    // so no separate latch registers needed — read directly from datapath wires.

    // ════════════════════════════════════════════════════════════════
    // Wishbone Slave
    // ════════════════════════════════════════════════════════════════

    logic wb_valid;
    assign wb_valid = wb_cyc_i && wb_stb_i;

    // Single-cycle ack
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            wb_ack_o <= 1'b0;
        else
            wb_ack_o <= wb_valid && !wb_ack_o;
    end

    // ── Register writes ──
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            reg_start      <= 1'b0;
            reg_clear      <= 1'b0;
            reg_continuous <= 1'b0;
            reg_pi_reset   <= 1'b0;
            reg_ia         <= '0;
            reg_ib         <= '0;
            reg_theta      <= '0;
            reg_id_ref     <= '0;
            reg_iq_ref     <= '0;
            reg_kp         <= '0;
            reg_ki         <= '0;
            reg_out_max    <= '0;
            reg_int_max    <= '0;
            reg_kp_q       <= '0;
            reg_ki_q       <= '0;
        end else begin
            // Auto-clear single-pulse control bits
            reg_start    <= 1'b0;
            reg_clear    <= 1'b0;
            reg_pi_reset <= 1'b0;

            if (wb_valid && wb_we_i && !wb_ack_o) begin
                case (wb_adr_i)
                    8'h00: begin  // CTRL
                        reg_start      <= wb_dat_i[0];
                        reg_clear      <= wb_dat_i[1];
                        reg_continuous <= wb_dat_i[2];
                        reg_pi_reset   <= wb_dat_i[3];
                    end
                    8'h08: reg_ia     <= wb_dat_i[DATA_W-1:0];
                    8'h0C: reg_ib     <= wb_dat_i[DATA_W-1:0];
                    8'h10: reg_theta  <= wb_dat_i[ANGLE_W-1:0];
                    8'h14: reg_id_ref <= wb_dat_i[DATA_W-1:0];
                    8'h18: reg_iq_ref <= wb_dat_i[DATA_W-1:0];
                    8'h1C: reg_kp     <= wb_dat_i[DATA_W-1:0];
                    8'h20: reg_ki     <= wb_dat_i[DATA_W-1:0];
                    8'h24: reg_out_max<= wb_dat_i[DATA_W-1:0];
                    8'h28: reg_int_max<= wb_dat_i[DATA_W-1:0];
                    8'h2C: reg_kp_q   <= wb_dat_i[DATA_W-1:0];
                    8'h30: reg_ki_q   <= wb_dat_i[DATA_W-1:0];
                    default: ;
                endcase
            end
        end
    end

    // ── Register reads ──
    always_comb begin
        wb_dat_o = 32'd0;
        case (wb_adr_i)
            8'h00: wb_dat_o = {28'd0, reg_pi_reset, reg_continuous, reg_clear, reg_start};
            8'h04: wb_dat_o = {cycle_count, 4'd0, 4'(state), 5'd0, status_error, status_done, status_busy};
            8'h08: wb_dat_o = {{(32-DATA_W){reg_ia[DATA_W-1]}}, reg_ia};
            8'h0C: wb_dat_o = {{(32-DATA_W){reg_ib[DATA_W-1]}}, reg_ib};
            8'h10: wb_dat_o = {{(32-ANGLE_W){1'b0}}, reg_theta};
            8'h14: wb_dat_o = {{(32-DATA_W){reg_id_ref[DATA_W-1]}}, reg_id_ref};
            8'h18: wb_dat_o = {{(32-DATA_W){reg_iq_ref[DATA_W-1]}}, reg_iq_ref};
            8'h1C: wb_dat_o = {{(32-DATA_W){reg_kp[DATA_W-1]}}, reg_kp};
            8'h20: wb_dat_o = {{(32-DATA_W){reg_ki[DATA_W-1]}}, reg_ki};
            8'h24: wb_dat_o = {{(32-DATA_W){reg_out_max[DATA_W-1]}}, reg_out_max};
            8'h28: wb_dat_o = {{(32-DATA_W){reg_int_max[DATA_W-1]}}, reg_int_max};
            8'h2C: wb_dat_o = {{(32-DATA_W){reg_kp_q[DATA_W-1]}}, reg_kp_q};
            8'h30: wb_dat_o = {{(32-DATA_W){reg_ki_q[DATA_W-1]}}, reg_ki_q};
            8'h40: wb_dat_o = {{(32-PWM_BITS){1'b0}}, w_duty_a};
            8'h44: wb_dat_o = {{(32-PWM_BITS){1'b0}}, w_duty_b};
            8'h48: wb_dat_o = {{(32-PWM_BITS){1'b0}}, w_duty_c};
            8'h50: wb_dat_o = {{(32-DATA_W){w_id[DATA_W-1]}}, w_id};
            8'h54: wb_dat_o = {{(32-DATA_W){w_iq[DATA_W-1]}}, w_iq};
            8'h58: wb_dat_o = {{(32-DATA_W){w_ialpha[DATA_W-1]}}, w_ialpha};
            8'h5C: wb_dat_o = {{(32-DATA_W){w_ibeta[DATA_W-1]}}, w_ibeta};
            8'h60: wb_dat_o = {{(32-DATA_W){w_vd[DATA_W-1]}}, w_vd};
            8'h64: wb_dat_o = {{(32-DATA_W){w_vq[DATA_W-1]}}, w_vq};
            8'h68: wb_dat_o = {{(32-DATA_W){w_valpha[DATA_W-1]}}, w_valpha};
            8'h6C: wb_dat_o = {{(32-DATA_W){w_vbeta[DATA_W-1]}}, w_vbeta};
            8'h70: wb_dat_o = 32'(dbg_pi_d_int);
            8'h74: wb_dat_o = 32'(dbg_pi_q_int);
            8'h80: wb_dat_o = {PWM_BITS[7:0], CORDIC_ITERS[7:0], FRAC_W[7:0], DATA_W[7:0]};
            8'h84: wb_dat_o = {12'd0, PIPE_DEPTH[1:0], SHARED_MUL[0], SINCOS_MODE[0], ANGLE_W[7:0], PI_ACC_W[7:0]};
            default: wb_dat_o = 32'd0;
        endcase
    end

    // ════════════════════════════════════════════════════════════════
    // Submodule Instantiations
    // ════════════════════════════════════════════════════════════════

    // ── CORDIC ──
    foc_cordic #(
        .DATA_W      (DATA_W),
        .FRAC_W      (FRAC_W),
        .ANGLE_W     (ANGLE_W),
        .CORDIC_ITERS(CORDIC_ITERS)
    ) u_cordic (
        .clk    (clk),
        .rst_n  (rst_n),
        .start  (cordic_start),
        .theta  (reg_theta),
        .cos_val(w_cos),
        .sin_val(w_sin),
        .done   (cordic_done)
    );

    // ── Clarke ──
    foc_clarke #(
        .DATA_W(DATA_W),
        .FRAC_W(FRAC_W)
    ) u_clarke (
        .clk    (clk),
        .rst_n  (rst_n),
        .en     (clarke_en),
        .ia     (reg_ia),
        .ib     (reg_ib),
        .i_alpha(w_ialpha),
        .i_beta (w_ibeta),
        .done   (clarke_done)
    );

    // ── Shared Rotation (Park + Inverse Park) ──
    // Park and InvPark never run concurrently, so they share one set of 4 multipliers.
    logic                      rotate_en;
    logic                      rotate_mode;  // 0=Park, 1=InvPark
    logic signed [DATA_W-1:0]  rotate_in_a, rotate_in_b;
    logic signed [DATA_W-1:0]  rotate_out_x, rotate_out_y;
    logic                      rotate_done;

    assign rotate_en   = park_en | inv_park_en;
    assign rotate_mode = inv_park_en;
    assign rotate_in_a = inv_park_en ? w_vd : w_ialpha;
    assign rotate_in_b = inv_park_en ? w_vq : w_ibeta;

    foc_rotate #(
        .DATA_W(DATA_W),
        .FRAC_W(FRAC_W)
    ) u_rotate (
        .clk    (clk),
        .rst_n  (rst_n),
        .en     (rotate_en),
        .mode   (rotate_mode),
        .in_a   (rotate_in_a),
        .in_b   (rotate_in_b),
        .sin_val(w_sin),
        .cos_val(w_cos),
        .out_x  (rotate_out_x),
        .out_y  (rotate_out_y),
        .done   (rotate_done)
    );

    // Rotate outputs feed PI directly (stable during PI stages since rotate
    // isn't re-enabled until InvPark, which runs after both PIs complete).
    // Hold registers capture Park results for Wishbone debug readback at 0x50/0x54,
    // since rotate outputs change when InvPark runs later.
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            w_id <= '0;
            w_iq <= '0;
        end else if (rotate_done && state == ST_PARK) begin
            w_id <= rotate_out_x;
            w_iq <= rotate_out_y;
        end
    end

    // InvPark outputs come directly from rotate (last user, outputs stay stable)
    assign w_valpha      = rotate_out_x;
    assign w_vbeta       = rotate_out_y;
    assign park_done     = rotate_done;
    assign inv_park_done = rotate_done;

    // ── PI D-axis ──
    logic pi_d_clear;
    assign pi_d_clear = reg_clear || reg_pi_reset;

    foc_pi #(
        .DATA_W  (DATA_W),
        .FRAC_W  (FRAC_W),
        .PI_ACC_W(PI_ACC_W)
    ) u_pi_d (
        .clk     (clk),
        .rst_n   (rst_n),
        .en      (pi_d_en),
        .clear   (pi_d_clear),
        .ref_val (reg_id_ref),
        .meas_val(rotate_out_x),
        .kp      (reg_kp),
        .ki      (reg_ki),
        .out_max (reg_out_max),
        .int_max (reg_int_max),
        .out_val (w_vd),
        .done    (pi_d_done)
    );

    // ── PI Q-axis ──
    // Use separate q-axis gains if non-zero, else fall back to d-axis gains
    logic signed [DATA_W-1:0] pi_q_kp, pi_q_ki;
    assign pi_q_kp = (reg_kp_q != '0) ? reg_kp_q : reg_kp;
    assign pi_q_ki = (reg_ki_q != '0) ? reg_ki_q : reg_ki;

    logic pi_q_clear;
    assign pi_q_clear = reg_clear || reg_pi_reset;

    foc_pi #(
        .DATA_W  (DATA_W),
        .FRAC_W  (FRAC_W),
        .PI_ACC_W(PI_ACC_W)
    ) u_pi_q (
        .clk     (clk),
        .rst_n   (rst_n),
        .en      (pi_q_en),
        .clear   (pi_q_clear),
        .ref_val (reg_iq_ref),
        .meas_val(rotate_out_y),
        .kp      (pi_q_kp),
        .ki      (pi_q_ki),
        .out_max (reg_out_max),
        .int_max (reg_int_max),
        .out_val (w_vq),
        .done    (pi_q_done)
    );

    // ── PI integrator debug readback ──
    assign dbg_pi_d_int = u_pi_d.u_i;
    assign dbg_pi_q_int = u_pi_q.u_i;

    // ── SVPWM ──
    foc_svpwm #(
        .DATA_W  (DATA_W),
        .FRAC_W  (FRAC_W),
        .PWM_BITS(PWM_BITS)
    ) u_svpwm (
        .clk    (clk),
        .rst_n  (rst_n),
        .en     (svpwm_en),
        .v_alpha(w_valpha),
        .v_beta (w_vbeta),
        .duty_a (w_duty_a),
        .duty_b (w_duty_b),
        .duty_c (w_duty_c),
        .done   (svpwm_done)
    );

endmodule
