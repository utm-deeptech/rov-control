/* Parameter storage: append-only records in one flash sector (wear levelling + power-loss safe).
 *
 *   record = [MAGIC][hash of the table][save counter][value 0..N-1][crc]
 *   load: the last valid record wins.  save: write into the next erased slot; erase the sector only when full.
 * A half-written record (power cut while saving) fails its crc and the previous one stays in use.
 * The hash covers names + count, so a firmware with a different table falls back to defaults instead of
 * loading values into the wrong parameters.
 */
#include "params.h"

#define MAGIC 0x334D5250u               /* "PRM3" */
#define REC_WORDS (5u + PARAM_COUNT)     /* magic, hash, counter, count, values, crc */

float param_v[PARAM_COUNT];

const param_info_t param_info[PARAM_COUNT] = {
#define X(n, d, lo, hi, fl) { #n, (float)(d), (float)(lo), (float)(hi), (uint8_t)(fl) },
    PARAM_LIST(X)
#undef X
};

static uint32_t next_slot, save_count;

static uint32_t crc32(const uint32_t *w, uint32_t n)
{
    uint32_t c = 0xFFFFFFFFu;
    while (n--) {
        uint32_t x = *w++;
        for (int b = 0; b < 32; b++) {
            uint32_t bit = ((c >> 31) ^ (x >> 31)) & 1u;
            c = (c << 1) ^ (bit ? 0x04C11DB7u : 0u);
            x <<= 1;
        }
    }
    return c;
}

uint32_t params_hash(void)
{
    uint32_t h = 2166136261u;                                  /* FNV-1a over every name */
    for (int i = 0; i < PARAM_COUNT; i++) {
        for (const char *s = param_info[i].name; *s; s++) { h ^= (uint8_t)*s; h *= 16777619u; }
        h ^= 0xFF; h *= 16777619u;
    }
    return h;
}

void params_defaults(void)
{
    for (int i = 0; i < PARAM_COUNT; i++) param_v[i] = param_info[i].def;
}

int params_set(int i, float v)
{
    if (i < 0 || i >= PARAM_COUNT || v != v) return 0;           /* index / NaN */
    const param_info_t *p = &param_info[i];
    if (v < p->min) v = p->min;
    if (v > p->max) v = p->max;
    if (p->flags & PF_INT) v = (float)(int)(v + (v >= 0 ? 0.5f : -0.5f));
    param_v[i] = v;
    return 1;
}

static uint32_t slots(void) { return board_flash_words() / REC_WORDS; }

static int slot_erased(const volatile uint32_t *r)
{
    for (uint32_t i = 0; i < REC_WORDS; i++) if (r[i] != 0xFFFFFFFFu) return 0;
    return 1;
}

int params_load(void)
{
    params_defaults();
    const volatile uint32_t *base = board_flash_base();
    int found = -1;
    uint32_t buf[REC_WORDS];
    next_slot = slots();                                        /* = full, unless an erased slot is found */
    save_count = 0;
    for (uint32_t s = 0; s < slots(); s++) {
        const volatile uint32_t *r = base + s * REC_WORDS;
        if (slot_erased(r)) { next_slot = s; break; }
        for (uint32_t i = 0; i < REC_WORDS; i++) buf[i] = r[i];
        if (buf[0] == MAGIC && buf[1] == params_hash() && crc32(buf, REC_WORDS - 1) == buf[REC_WORDS - 1]) {
            found = (int)s;
            save_count = buf[2];
        }
    }
    if (found < 0) return 0;
    const volatile uint32_t *r = base + (uint32_t)found * REC_WORDS;
    for (int i = 0; i < PARAM_COUNT; i++) {
        union { uint32_t u; float f; } x; x.u = r[4 + i];
        if (!params_set(i, x.f)) param_v[i] = param_info[i].def;
    }
    return 1;
}

int params_save(void)
{
    uint32_t rec[REC_WORDS];
    rec[0] = MAGIC; rec[1] = params_hash(); rec[2] = ++save_count; rec[3] = PARAM_COUNT;
    for (int i = 0; i < PARAM_COUNT; i++) { union { float f; uint32_t u; } x; x.f = param_v[i]; rec[4 + i] = x.u; }
    rec[REC_WORDS - 1] = crc32(rec, REC_WORDS - 1);
    if (next_slot >= slots()) {
        if (board_flash_erase()) return 2;
        next_slot = 0;
    }
    if (board_flash_write(next_slot * REC_WORDS, rec, REC_WORDS)) { next_slot++; return 3; }
    const volatile uint32_t *r = board_flash_base() + next_slot * REC_WORDS;
    for (uint32_t i = 0; i < REC_WORDS; i++) if (r[i] != rec[i]) { next_slot++; return 4; }  /* verify */
    next_slot++;
    return 0;
}

int params_saves_left(void) { return (int)(slots() - next_slot); }
