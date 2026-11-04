/* Board-independent main loop.
 *
 *   SysTick 1 kHz ──> ms counter (the only interrupt we use)
 *   main loop, woken every ms:
 *     UART RX (DMA ring)  ┐
 *     SWD  RX (mailbox)   ┴─> core_rx_port()  ->  core_tick(): failsafe, 100 Hz control, telemetry
 *     core_tx_take() ──> UART TX (DMA) + SWD TX (mailbox)
 *     outputs -> timers, LED, watchdog, loop timing
 *
 * No RTOS: there are only three jobs and they all finish in well under a millisecond, so one loop with
 * DMA doing the byte-shovelling is simpler to reason about than tasks + locks. The STATUS telemetry
 * reports the measured loop time / CPU load so that claim stays checkable.
 *
 * LED PC13: ON = armed | fast blink = failsafe | disarmed & linked: follows lights | short flash every 2 s = no link
 *
 * SWD mailbox ("virtual UART" for the bench when the UART wires aren't connected): two byte rings in RAM that a
 * debugger reads/writes while the chip runs (bench/swd_link.py). Same frames as the UART, so everything above
 * the wire is identical.
 */
#include "board.h"
#include "core.h"

#define SWD_IN  512
#define SWD_OUT 2048
struct swdlink {
    uint32_t magic;                    /* 'SWDL' once the firmware is running */
    uint32_t in_head, in_tail;         /* host writes in[] + in_head; firmware advances in_tail */
    uint32_t out_head, out_tail;       /* firmware writes out[] + out_head; host advances out_tail */
    uint32_t in_size, out_size;
    uint32_t host_seen;                /* host sets this to 1: firmware then mirrors TX into out[] */
    uint8_t  in[SWD_IN];
    uint8_t  out[SWD_OUT];
};
volatile struct swdlink swdlink;

static volatile uint32_t ms;
void app_tick_1ms(void) { ms++; }

static void led(uint32_t now)
{
    int on;
    if (core_armed())            on = 1;
    else if (core_failsafe())    on = (now / 100) & 1;
    else if (core_linked(now))   on = core_lights() > LIGHTS_OFF_US;
    else                         on = (now % 2000) < 100;
    board_led(on);
}

static void swd_rx(uint32_t now)
{
    uint32_t h = swdlink.in_head, t = swdlink.in_tail;
    if (h >= SWD_IN) return;
    while (t != h) {
        core_rx_port(1, swdlink.in[t], now);
        t = (t + 1) % SWD_IN;
    }
    swdlink.in_tail = t;
}

static void swd_tx(const uint8_t *p, int n)
{
    if (!swdlink.host_seen) return;
    uint32_t h = swdlink.out_head, t = swdlink.out_tail;
    if (t >= SWD_OUT) return;
    uint32_t used = (h + SWD_OUT - t) % SWD_OUT;
    if ((int)(SWD_OUT - 1 - used) < n) return;          /* whole chunk (whole frames) or nothing */
    for (int i = 0; i < n; i++) { swdlink.out[h] = p[i]; h = (h + 1) % SWD_OUT; }
    swdlink.out_head = h;
}

int main(void)
{
    swdlink.in_head = swdlink.in_tail = swdlink.out_head = swdlink.out_tail = 0;
    swdlink.in_size = SWD_IN; swdlink.out_size = SWD_OUT; swdlink.host_seen = 0;
    swdlink.magic = 0x4C445753u;
    core_init();
    board_init();

    uint8_t  rx[128], tx[256];
    int      txn = 0, txoff = 0;
    uint32_t last_ms = ms, busy = 0, win_start = board_cycles(), max_us = 0, sum_us = 0, n_loops = 0;
    uint16_t out[N_OUTPUTS];

    for (;;) {
        while (ms == last_ms) board_idle();             /* WFI until SysTick */
        uint32_t c0 = board_cycles();
        uint32_t now = last_ms = ms;
        board_kick_watchdog();

        int n;
        while ((n = board_uart_read(rx, sizeof rx)) > 0)
            for (int i = 0; i < n; i++) core_rx_port(0, rx[i], now);
        swd_rx(now);

        core_tick(now);
        for (int i = 0; i < N_OUTPUTS; i++) out[i] = core_pwm(i);
        out[OUT_LIGHTS] = core_lights();
        out[OUT_GIMBAL] = core_gimbal();
        board_outputs(out);

        if (txoff >= txn) {                              /* previous chunk fully handed to the UART */
            txn = core_tx_take(tx, sizeof tx);
            txoff = 0;
            if (txn) swd_tx(tx, txn);
        }
        if (txoff < txn) txoff += board_uart_write(tx + txoff, txn - txoff);
        led(now);

        /* timing: how long this pass took, and the share of each second spent working */
        uint32_t dc = board_cycles() - c0, us = dc / board_cycles_per_us();
        busy += dc; sum_us += us; n_loops++;
        if (us > max_us) max_us = us;
        uint32_t win = board_cycles() - win_start;
        if (win >= 1000000u * board_cycles_per_us()) {   /* every second */
            core_set_timing((uint16_t)(sum_us / n_loops), (uint16_t)max_us,
                            (uint16_t)(busy / (win / 1000u)));
            busy = sum_us = n_loops = max_us = 0;
            win_start = board_cycles();
        }
    }
}
