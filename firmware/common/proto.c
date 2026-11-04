#include "proto.h"

uint16_t proto_crc16(const uint8_t *d, int n)
{
    uint16_t crc = 0xFFFF;
    while (n--) {
        crc ^= (uint16_t)(*d++) << 8;
        for (int i = 0; i < 8; i++)
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    }
    return crc;
}

void parser_init(parser_t *p) { p->n = p->used = 0; p->bad = 0; }

static void drop(parser_t *p, int k)
{
    for (int i = 0; i + k < p->n; i++) p->buf[i] = p->buf[i + k];
    p->n -= k;
}

void parser_push(parser_t *p, uint8_t b)
{
    if (p->used) { drop(p, p->used); p->used = 0; }
    if (p->n >= (int)sizeof p->buf) { drop(p, 1); p->bad++; }   /* unreachable if popped after every push */
    p->buf[p->n++] = b;
}

int parser_pop(parser_t *p, frame_t *f)
{
    if (p->used) { drop(p, p->used); p->used = 0; }
    for (;;) {
        if (p->n >= 1 && p->buf[0] != 0xAA) { drop(p, 1); continue; }
        if (p->n >= 2 && p->buf[1] != 0x55) { drop(p, 1); continue; }
        if (p->n < 5) return 0;
        int len = p->buf[2];
        if (len > PROTO_MAX_LEN) { p->bad++; drop(p, 1); continue; }
        int total = PROTO_OVERHEAD + len;
        if (p->n < total) return 0;
        if (proto_crc16(&p->buf[2], 3 + len) != get16(&p->buf[5 + len])) {
            p->bad++; drop(p, 1);                 /* a real frame may start inside the corrupted one */
            continue;
        }
        f->len = (uint8_t)len; f->id = p->buf[3]; f->seq = p->buf[4]; f->p = &p->buf[5];
        p->used = total;
        return 1;
    }
}

int proto_encode(uint8_t *f, uint8_t id, uint8_t seq, const uint8_t *payload, int len)
{
    f[0] = 0xAA; f[1] = 0x55; f[2] = (uint8_t)len; f[3] = id; f[4] = seq;
    for (int i = 0; i < len; i++) f[5 + i] = payload[i];
    put16(&f[5 + len], proto_crc16(&f[2], 3 + len));
    return PROTO_OVERHEAD + len;
}
