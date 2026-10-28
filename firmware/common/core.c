#include "core.h"

#define MSG_HEARTBEAT 0x01
#define MSG_ARM       0x02
#define MSG_THRUSTERS 0x03
#define MSG_LIGHTS    0x04
#define MSG_TELEMETRY 0x81
#define MSG_ACK       0x82
#define MAX_LEN       64
#define OVERHEAD      7

static uint8_t  rxbuf[OVERHEAD + MAX_LEN];
static int      rxn;
static uint8_t  txbuf[160];
static int      txn;
static uint8_t  tx_seq;
static uint16_t pwm[N_THRUSTERS];
static uint16_t lights;
static int      armed, failsafe;
static uint16_t rx_ok, rx_bad;
static uint32_t last_link, last_telem;
static int      ever_linked;

uint16_t core_crc16(const uint8_t *d, int n)
{
    uint16_t crc = 0xFFFF;
    while (n--) {
        crc ^= (uint16_t)(*d++) << 8;
        for (int i = 0; i < 8; i++)
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    }
    return crc;
}

static void all_center(void)
{
    for (int i = 0; i < N_THRUSTERS; i++) pwm[i] = PWM_CENTER;
}

void core_init(void)
{
    rxn = txn = 0;
    tx_seq = 0;
    armed = failsafe = 0;
    rx_ok = rx_bad = 0;
    last_link = last_telem = 0;
    ever_linked = 0;
    lights = LIGHTS_OFF_US;
    all_center();
}

static void send(uint8_t id, const uint8_t *payload, int len)
{
    if (txn + OVERHEAD + len > (int)sizeof txbuf) return;      /* never block the control loop */
    uint8_t *f = &txbuf[txn];
    f[0] = 0xAA; f[1] = 0x55; f[2] = (uint8_t)len; f[3] = id; f[4] = ++tx_seq;
    for (int i = 0; i < len; i++) f[5 + i] = payload[i];
    uint16_t c = core_crc16(&f[2], 3 + len);
    f[5 + len] = (uint8_t)c;
    f[6 + len] = (uint8_t)(c >> 8);
    txn += OVERHEAD + len;
}

static void send_telemetry(uint32_t now)
{
    uint8_t p[12 + 2 * N_THRUSTERS];
    p[0] = (uint8_t)now; p[1] = (uint8_t)(now >> 8); p[2] = (uint8_t)(now >> 16); p[3] = (uint8_t)(now >> 24);
    p[4] = (uint8_t)armed; p[5] = (uint8_t)failsafe;
    p[6] = (uint8_t)rx_ok;  p[7] = (uint8_t)(rx_ok >> 8);
    p[8] = (uint8_t)rx_bad; p[9] = (uint8_t)(rx_bad >> 8);
    p[10] = (uint8_t)lights; p[11] = (uint8_t)(lights >> 8);
    for (int i = 0; i < N_THRUSTERS; i++) {
        p[12 + 2 * i] = (uint8_t)pwm[i];
        p[13 + 2 * i] = (uint8_t)(pwm[i] >> 8);
    }
    send(MSG_TELEMETRY, p, sizeof p);
}

static uint16_t cmd_to_pwm(int v)
{
    if (v > 1000) v = 1000;
    if (v < -1000) v = -1000;
    int x = v * 400;
    int q = x / 1000;                       /* floor like python's // */
    if (x % 1000 < 0) q--;
    q += PWM_CENTER;
    return (uint16_t)(q < PWM_MIN ? PWM_MIN : q > PWM_MAX ? PWM_MAX : q);
}

static void handle(uint8_t id, uint8_t seq, const uint8_t *p, int len, uint32_t now)
{
    switch (id) {
    case MSG_HEARTBEAT:
        if (len != 4) { rx_bad++; return; }
        break;
    case MSG_ARM:
        if (len != 1) { rx_bad++; return; }
        if (!(armed && p[0]))               /* a repeated ARM while armed must not blip the thrusters */
            all_center();                   /* arming: stay at 1500 until the next THRUSTERS frame */
        armed = p[0] != 0;
        failsafe = 0;
        { uint8_t a[2] = { MSG_ARM, seq }; send(MSG_ACK, a, 2); }
        break;
    case MSG_THRUSTERS:
        if (len != 2 * N_THRUSTERS) { rx_bad++; return; }
        if (armed)
            for (int i = 0; i < N_THRUSTERS; i++)
                pwm[i] = cmd_to_pwm((int16_t)(p[2 * i] | (p[2 * i + 1] << 8)));
        break;
    case MSG_LIGHTS:                        /* lights work armed or disarmed, and survive failsafe */
        if (len != 2) { rx_bad++; return; }
        {
            int lv = p[0] | (p[1] << 8);
            if (lv > 1000) lv = 1000;
            lights = (uint16_t)(LIGHTS_OFF_US + lv * (LIGHTS_FULL_US - LIGHTS_OFF_US) / 1000);
        }
        break;
    default:
        rx_bad++;                           /* unknown id: bad, and must not reset the link timer */
        return;
    }
    rx_ok++;
    last_link = now;
    ever_linked = 1;
}

static void shift(int n)
{
    for (int i = 0; i + n < rxn; i++) rxbuf[i] = rxbuf[i + n];
    rxn -= n;
}

void core_rx(uint8_t b, uint32_t now)
{
    rxbuf[rxn++] = b;
    for (;;) {
        if (rxn >= 1 && rxbuf[0] != 0xAA) { shift(1); continue; }
        if (rxn >= 2 && rxbuf[1] != 0x55) { shift(1); continue; }
        if (rxn < 5) return;
        int len = rxbuf[2];
        if (len > MAX_LEN) { rx_bad++; shift(1); continue; }
        int total = OVERHEAD + len;
        if (rxn < total) return;
        uint16_t got = (uint16_t)(rxbuf[5 + len] | (rxbuf[6 + len] << 8));
        if (core_crc16(&rxbuf[2], 3 + len) != got) { rx_bad++; shift(1); continue; }
        handle(rxbuf[3], rxbuf[4], &rxbuf[5], len, now);
        shift(total);
    }
}

void core_tick(uint32_t now)
{
    if (armed && (uint32_t)(now - last_link) > FAILSAFE_MS) {
        all_center();
        armed = 0;
        failsafe = 1;
    }
    if ((uint32_t)(now - last_telem) >= TELEM_MS) {
        last_telem = now;
        send_telemetry(now);
    }
}

int core_tx_take(uint8_t *dst, int max)
{
    int n = txn < max ? txn : max;
    for (int i = 0; i < n; i++) dst[i] = txbuf[i];
    for (int i = n; i < txn; i++) txbuf[i - n] = txbuf[i];
    txn -= n;
    return n;
}

uint16_t core_pwm(int i)      { return pwm[i]; }
uint16_t core_lights(void)    { return lights; }
uint16_t core_rx_ok(void)     { return rx_ok; }
uint16_t core_rx_bad(void)    { return rx_bad; }
int      core_armed(void)     { return armed; }
int      core_failsafe(void)  { return failsafe; }
int      core_linked(uint32_t now) { return ever_linked && (uint32_t)(now - last_link) <= FAILSAFE_MS; }
