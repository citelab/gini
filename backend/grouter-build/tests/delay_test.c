/*
 * delay_test.c — standalone unit test for gr_delay.c (libc + pthreads + libm only).
 *
 * Verifies the delay line without the rest of the gRouter:
 *   1. order preservation (FIFO) — even under IID jitter that forces the backstop clamp
 *   2. release monotonicity (emit timestamps never go backwards)
 *   3. timing accuracy (mean hold ~= base delay)
 *   4. jitter present and positively autocorrelated when corr is high
 *   5. bounded holding queue: overflow drops, held <= limit, caller keeps ownership
 *
 * Build:  gcc -O2 -I../../include delay_test.c ../../src/grouter/gr_delay.c -lpthread -lm -o delay_test
 * Run:    ./delay_test         (exit 0 = all pass)
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <pthread.h>
#include <unistd.h>

#include "gr_delay.h"

static double now_ms(void)
{
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}
static void sleep_ms(double ms)
{
    struct timespec ts; ts.tv_sec = (time_t)(ms/1000.0);
    ts.tv_nsec = (long)((ms - ts.tv_sec*1000.0) * 1e6);
    nanosleep(&ts, NULL);
}

typedef struct { int id; double push_t; } tpkt_t;

/* recorder written by the (single) release thread, read by main after quiescing */
#define MAXREC 4096
static pthread_mutex_t rlock = PTHREAD_MUTEX_INITIALIZER;
static int    rec_id[MAXREC];
static double rec_emit_t[MAXREC];
static double rec_hold[MAXREC];
static int    rec_n;

static void emit_cb(void *p)
{
    tpkt_t *t = (tpkt_t *)p;
    double t_now = now_ms();
    pthread_mutex_lock(&rlock);
    if (rec_n < MAXREC) {
        rec_id[rec_n]     = t->id;
        rec_emit_t[rec_n] = t_now;
        rec_hold[rec_n]   = t_now - t->push_t;
        rec_n++;
    }
    pthread_mutex_unlock(&rlock);
    free(t);
}

static void reset_rec(void){ pthread_mutex_lock(&rlock); rec_n = 0; pthread_mutex_unlock(&rlock); }
static int  rec_count(void){ int n; pthread_mutex_lock(&rlock); n = rec_n; pthread_mutex_unlock(&rlock); return n; }

static int fails = 0;
static void check(const char *name, int ok, const char *detail)
{
    printf("  [%s] %s%s%s\n", ok ? "PASS" : "FAIL", name,
           detail && *detail ? " — " : "", detail ? detail : "");
    if (!ok) fails++;
}

/* push n packets ids 0..n-1 into dl, inter_ms apart; returns #accepted */
static int push_stream(gr_delayline_t *dl, int n, double inter_ms)
{
    int acc = 0;
    for (int i = 0; i < n; i++) {
        tpkt_t *t = malloc(sizeof(*t));
        t->id = i; t->push_t = now_ms();
        if (gr_delay_push(dl, t)) acc++;
        else free(t);                 /* dropped: caller still owns it */
        if (inter_ms > 0) sleep_ms(inter_ms);
    }
    return acc;
}

/* wait until `want` packets have been released (or timeout) */
static int wait_drain(gr_delayline_t *dl, int want, double timeout_ms)
{
    double t0 = now_ms();
    while (gr_delay_passed(dl) < want) {
        if (now_ms() - t0 > timeout_ms) return 0;
        sleep_ms(2);
    }
    return 1;
}

/* ---- Test 1: order + monotonic + timing + autocorrelation (correlated jitter) ---------- */
static void test_ordered_timing(void)
{
    printf("Test 1: order / monotonic release / timing / autocorrelation\n");
    const int N = 200; const double BASE = 20.0, JIT = 5.0, CORR = 0.85;
    reset_rec();
    gr_delayline_t *dl = gr_delay_create(emit_cb, BASE, JIT, CORR, 0);
    check("create", dl != NULL, "");
    if (!dl) return;

    push_stream(dl, N, 2.0);                 /* ~2ms apart, base 20ms => rarely clamped */
    int drained = wait_drain(dl, N, 5000);
    check("all released", drained && rec_count() == N, "");

    /* order preserved: ids emitted 0,1,2,... */
    int ordered = 1;
    for (int i = 0; i < rec_n; i++) if (rec_id[i] != i) { ordered = 0; break; }
    check("order preserved (FIFO)", ordered, "");

    /* release timestamps non-decreasing */
    int mono = 1;
    for (int i = 1; i < rec_n; i++) if (rec_emit_t[i] < rec_emit_t[i-1] - 1e-6) { mono = 0; break; }
    check("release monotonic", mono, "");

    /* mean hold ~= base (loose bounds for scheduler jitter) */
    double sum = 0; for (int i = 0; i < rec_n; i++) sum += rec_hold[i];
    double mean = sum / rec_n;
    char buf[128]; snprintf(buf, sizeof buf, "mean hold %.1f ms (base %.0f)", mean, BASE);
    check("timing: mean hold near base", mean > BASE*0.6 && mean < BASE*2.2, buf);

    /* jitter present */
    double var = 0; for (int i = 0; i < rec_n; i++){ double d = rec_hold[i]-mean; var += d*d; }
    double sd = sqrt(var / rec_n);
    snprintf(buf, sizeof buf, "hold std-dev %.2f ms", sd);
    check("jitter present (std-dev > 0)", sd > 0.5, buf);

    /* lag-1 autocorrelation of hold times > 0 for high corr */
    double c0 = 0, c1 = 0;
    for (int i = 0; i < rec_n; i++) c0 += (rec_hold[i]-mean)*(rec_hold[i]-mean);
    for (int i = 1; i < rec_n; i++) c1 += (rec_hold[i]-mean)*(rec_hold[i-1]-mean);
    double ac1 = c1 / c0;
    snprintf(buf, sizeof buf, "lag-1 autocorr %.2f (corr=%.2f)", ac1, CORR);
    check("correlated jitter (autocorr > 0.1)", ac1 > 0.1, buf);

    gr_delay_destroy(dl);
}

/* ---- Test 2: order preserved under IID jitter that forces the clamp -------------------- */
static void test_order_under_clamp(void)
{
    printf("Test 2: order preserved under heavy IID jitter (clamp stress)\n");
    const int N = 300;
    reset_rec();
    gr_delayline_t *dl = gr_delay_create(emit_cb, 10.0, 8.0, 0.0, 0);  /* IID, big jitter */
    if (!dl) { check("create", 0, ""); return; }
    push_stream(dl, N, 0.5);                 /* fast pushes => many would-be reorderings */
    int drained = wait_drain(dl, N, 6000);
    check("all released", drained && rec_count() == N, "");
    int ordered = 1;
    for (int i = 0; i < rec_n; i++) if (rec_id[i] != i) { ordered = 0; break; }
    check("order preserved despite clamp", ordered, "");
    gr_delay_destroy(dl);
}

/* ---- Test 3: bounded holding queue drops on overflow, caller keeps ownership ----------- */
static void test_bound(void)
{
    printf("Test 3: bounded holding queue (overflow drop)\n");
    const int N = 200, LIMIT = 50;
    reset_rec();
    gr_delayline_t *dl = gr_delay_create(emit_cb, 400.0, 0.0, 0.0, LIMIT); /* long hold */
    if (!dl) { check("create", 0, ""); return; }
    int acc = push_stream(dl, N, 0.0);       /* burst: hold is long so they pile up */
    long held = gr_delay_held(dl), dropped = gr_delay_dropped(dl);
    char buf[160];
    snprintf(buf, sizeof buf, "accepted %d, held %ld, dropped %ld (limit %d)", acc, held, dropped, LIMIT);
    check("held never exceeds limit", held <= LIMIT, buf);
    check("overflow produced drops", dropped > 0 && acc <= LIMIT, "");
    check("accepted + dropped == pushed", acc + (int)dropped == N, "");
    gr_delay_destroy(dl);                     /* frees the still-held nodes */
}

int main(void)
{
    printf("=== gr_delay unit tests ===\n");
    test_ordered_timing();
    test_order_under_clamp();
    test_bound();
    printf("\n%s (%d failure%s)\n", fails ? "FAILED" : "ALL PASSED", fails, fails==1?"":"s");
    return fails ? 1 : 0;
}
