#include "mixer.h"
#include "params.h"

#define M(t, d) param_v[P_MIX_T1_SURGE + (t) * N_DOF + (d)]

static float absf(float x) { return x < 0 ? -x : x; }

void mixer_mix(const float dof[N_DOF], float thr[N_THR])
{
    float peak = 0;
    for (int t = 0; t < N_THR; t++) {
        float s = 0;
        for (int d = 0; d < N_DOF; d++) s += M(t, d) * dof[d];
        thr[t] = s;
        if (absf(s) > peak) peak = absf(s);
    }
    float k = (peak > 1.0f ? 1.0f / peak : 1.0f) * P(THR_LIMIT);
    for (int t = 0; t < N_THR; t++) thr[t] *= k;
}

void mixer_achieved(const float thr[N_THR], float dof[N_DOF])
{
    for (int d = 0; d < N_DOF; d++) {
        float num = 0, den = 0;
        for (int t = 0; t < N_THR; t++) { num += M(t, d) * thr[t]; den += M(t, d) * M(t, d); }
        dof[d] = den > 0 ? num / den : 0;
    }
}

int mixer_output_of(int t) { return (int)param_v[P_THR_OUT_T1 + t] - 1; }

int mixer_outputs_ok(void)
{
    unsigned used = 0;
    for (int t = 0; t < N_THR; t++) {
        int o = mixer_output_of(t);
        if (o < 0 || o >= N_THR || (used & (1u << o))) return 0;
        used |= 1u << o;
    }
    return 1;
}
