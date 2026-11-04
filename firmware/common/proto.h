/* Link framing (see protocol/PROTOCOL.md): AA 55 len id seq payload crc16. No hardware access. */
#ifndef PROTO_H
#define PROTO_H
#include <stdint.h>

#define PROTO_MAX_LEN  64
#define PROTO_OVERHEAD 7

typedef struct {
    uint8_t  id, seq, len;
    const uint8_t *p;          /* payload, valid until the next parser call */
} frame_t;

typedef struct {
    uint8_t  buf[PROTO_OVERHEAD + PROTO_MAX_LEN];
    int      n;                /* bytes in buf */
    int      used;             /* length of the frame last returned by parser_pop (removed on the next call) */
    uint16_t bad;              /* resync events: bad CRC / oversize len */
} parser_t;

uint16_t proto_crc16(const uint8_t *d, int n);
void     parser_init(parser_t *p);
void     parser_push(parser_t *p, uint8_t b);
int      parser_pop(parser_t *p, frame_t *f);     /* 1 = a CRC-valid frame is in *f; call until it returns 0 */
int      proto_encode(uint8_t *dst, uint8_t id, uint8_t seq, const uint8_t *payload, int len);

/* little-endian helpers */
static inline void     put16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static inline void     put32(uint8_t *p, uint32_t v) { put16(p, (uint16_t)v); put16(p + 2, (uint16_t)(v >> 16)); }
static inline uint16_t get16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static inline uint32_t get32(const uint8_t *p) { return get16(p) | ((uint32_t)get16(p + 2) << 16); }
static inline void putf(uint8_t *p, float f) { union { float f; uint32_t u; } x; x.f = f; put32(p, x.u); }
static inline float getf(const uint8_t *p) { union { float f; uint32_t u; } x; x.u = get32(p); return x.f; }
#endif
