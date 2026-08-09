/*
 * gr_delay.c  —  link-delay lines for the gRouter.  See gr_delay.h for the design.
 *
 * A line is a FIFO of held packets, each stamped with a monotonic release time. One release
 * thread waits until the head packet's time is up, pops it, and calls emit() (with the lock
 * released, so a slow emit never stalls producers). Producers (gr_delay_push) just append and,
 * if the queue was empty, wake the release thread. Nothing here touches the gRouter's data
 * structures, so it is independently testable.
 */
#include <stdlib.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <pthread.h>

#include "gr_delay.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

typedef struct dnode
{
    void          *pkt;
    double         release;   /* CLOCK_MONOTONIC seconds */
    struct dnode  *next;
} dnode_t;

struct gr_delayline
{
    gr_delay_emit_fn emit;

    /* config (guarded by lock) */
    double base_ms;
    double jitter_ms;
    double corr;
    int    limit;

    /* jitter sampler state (AR(1)) + PRNG */
    double   j_state;       /* current correlated jitter component, milliseconds */
    uint64_t rng;           /* xorshift64 state */
    int      have_spare;    /* Box-Muller cache */
    double   spare;

    /* FIFO holding queue */
    dnode_t *head, *tail;
    long     count;
    double   last_release;  /* release time of the most recently enqueued packet (clamp) */

    /* stats */
    long passed;
    long dropped;

    /* threading */
    pthread_mutex_t  lock;
    pthread_cond_t   cv;        /* uses CLOCK_MONOTONIC */
    pthread_t        thread;
    int              running;
};

/* ---- helpers ---------------------------------------------------------------------------- */

static double now_mono(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
}

static void abstime_from(double t, struct timespec *ts)
{
    if (t < 0) t = 0;
    ts->tv_sec  = (time_t)t;
    ts->tv_nsec = (long)((t - (double)ts->tv_sec) * 1e9);
    if (ts->tv_nsec >= 1000000000L) { ts->tv_sec++; ts->tv_nsec -= 1000000000L; }
    if (ts->tv_nsec < 0) ts->tv_nsec = 0;
}

static uint64_t xorshift64(uint64_t *s)
{
    uint64_t x = *s;
    x ^= x << 13; x ^= x >> 7; x ^= x << 17;
    return (*s = x);
}

/* uniform in (0,1) */
static double urand(gr_delayline_t *dl)
{
    return ((double)(xorshift64(&dl->rng) >> 11) + 1.0) / 9007199254740993.0;
}

/* standard normal via Box-Muller (cached spare) */
static double gauss(gr_delayline_t *dl)
{
    if (dl->have_spare) { dl->have_spare = 0; return dl->spare; }
    double u1 = urand(dl), u2 = urand(dl);
    double r = sqrt(-2.0 * log(u1)), a = 2.0 * M_PI * u2;
    dl->spare = r * sin(a);
    dl->have_spare = 1;
    return r * cos(a);
}

/* Sample the next delay (ms), advancing the AR(1) jitter state. Caller holds the lock. */
static double sample_delay_ms(gr_delayline_t *dl)
{
    double d;
    if (dl->jitter_ms <= 0.0)
    {
        dl->j_state = 0.0;
        d = dl->base_ms;
    }
    else
    {
        double rho = dl->corr;
        if (rho < 0.0) rho = 0.0;
        if (rho > 0.999) rho = 0.999;
        /* AR(1) with stationary std-dev == jitter_ms:  j = rho*j + sqrt(1-rho^2)*sigma*z */
        dl->j_state = rho * dl->j_state + sqrt(1.0 - rho * rho) * dl->jitter_ms * gauss(dl);
        d = dl->base_ms + dl->j_state;
    }
    if (d < 0.0) d = 0.0;   /* delay has a hard floor */
    return d;
}

/* ---- release thread --------------------------------------------------------------------- */

static void *release_loop(void *arg)
{
    gr_delayline_t *dl = (gr_delayline_t *)arg;
    pthread_mutex_lock(&dl->lock);
    while (dl->running)
    {
        if (dl->head == NULL)
        {
            pthread_cond_wait(&dl->cv, &dl->lock);   /* woken by a push or by destroy */
            continue;
        }
        double t = dl->head->release;
        double now = now_mono();
        if (now < t)
        {
            struct timespec ts;
            abstime_from(t, &ts);
            pthread_cond_timedwait(&dl->cv, &dl->lock, &ts);  /* wake at release or on push */
            continue;
        }
        /* head is due — pop it and emit with the lock released */
        dnode_t *n = dl->head;
        dl->head = n->next;
        if (dl->head == NULL) dl->tail = NULL;
        dl->count--;
        dl->passed++;
        pthread_mutex_unlock(&dl->lock);

        dl->emit(n->pkt);
        free(n);

        pthread_mutex_lock(&dl->lock);
    }
    pthread_mutex_unlock(&dl->lock);
    return NULL;
}

/* ---- public API ------------------------------------------------------------------------- */

gr_delayline_t *gr_delay_create(gr_delay_emit_fn emit,
                                double base_ms, double jitter_ms, double corr, int limit)
{
    if (emit == NULL) return NULL;
    gr_delayline_t *dl = (gr_delayline_t *)calloc(1, sizeof(*dl));
    if (!dl) return NULL;

    dl->emit  = emit;
    dl->rng   = 0x9e3779b97f4a7c15ULL ^ (uint64_t)(size_t)dl
                ^ ((uint64_t)time(NULL) << 21);
    if (dl->rng == 0) dl->rng = 0x123456789ULL;
    dl->last_release = now_mono();

    pthread_mutex_init(&dl->lock, NULL);
    pthread_condattr_t ca;
    pthread_condattr_init(&ca);
    pthread_condattr_setclock(&ca, CLOCK_MONOTONIC);
    pthread_cond_init(&dl->cv, &ca);
    pthread_condattr_destroy(&ca);

    gr_delay_config(dl, base_ms, jitter_ms, corr, limit);

    dl->running = 1;
    if (pthread_create(&dl->thread, NULL, release_loop, dl) != 0)
    {
        pthread_cond_destroy(&dl->cv);
        pthread_mutex_destroy(&dl->lock);
        free(dl);
        return NULL;
    }
    return dl;
}

void gr_delay_config(gr_delayline_t *dl,
                     double base_ms, double jitter_ms, double corr, int limit)
{
    if (!dl) return;
    if (base_ms < 0)   base_ms = 0;
    if (jitter_ms < 0) jitter_ms = 0;
    if (corr < 0)      corr = 0;
    if (corr > 0.999)  corr = 0.999;
    if (limit <= 0)    limit = GR_DELAY_DEFAULT_LIMIT;

    pthread_mutex_lock(&dl->lock);
    dl->base_ms   = base_ms;
    dl->jitter_ms = jitter_ms;
    dl->corr      = corr;
    dl->limit     = limit;
    pthread_mutex_unlock(&dl->lock);
}

int gr_delay_push(gr_delayline_t *dl, void *pkt)
{
    if (!dl) return 0;
    pthread_mutex_lock(&dl->lock);

    if (dl->count >= dl->limit)
    {
        dl->dropped++;
        pthread_mutex_unlock(&dl->lock);
        return 0;   /* full — caller keeps ownership */
    }

    dnode_t *n = (dnode_t *)malloc(sizeof(*n));
    if (!n)
    {
        dl->dropped++;
        pthread_mutex_unlock(&dl->lock);
        return 0;
    }

    double now = now_mono();
    double d   = sample_delay_ms(dl);
    double release = now + d / 1000.0;
    /* order-preserving backstop: never release before the previous packet did */
    if (release < dl->last_release) release = dl->last_release;
    dl->last_release = release;

    n->pkt = pkt;
    n->release = release;
    n->next = NULL;

    int was_empty = (dl->head == NULL);
    if (was_empty) dl->head = dl->tail = n;
    else { dl->tail->next = n; dl->tail = n; }
    dl->count++;

    if (was_empty) pthread_cond_signal(&dl->cv);   /* wake the release thread */
    pthread_mutex_unlock(&dl->lock);
    return 1;
}

long   gr_delay_held(gr_delayline_t *dl)    { return dl ? dl->count : 0; }
long   gr_delay_passed(gr_delayline_t *dl)  { return dl ? dl->passed : 0; }
long   gr_delay_dropped(gr_delayline_t *dl) { return dl ? dl->dropped : 0; }
double gr_delay_base_ms(gr_delayline_t *dl) { return dl ? dl->base_ms : 0; }
double gr_delay_jitter_ms(gr_delayline_t *dl){ return dl ? dl->jitter_ms : 0; }
double gr_delay_corr(gr_delayline_t *dl)    { return dl ? dl->corr : 0; }
int    gr_delay_limit(gr_delayline_t *dl)   { return dl ? dl->limit : 0; }

void gr_delay_destroy(gr_delayline_t *dl)
{
    if (!dl) return;
    pthread_mutex_lock(&dl->lock);
    dl->running = 0;
    pthread_cond_signal(&dl->cv);
    pthread_mutex_unlock(&dl->lock);
    pthread_join(dl->thread, NULL);

    dnode_t *n = dl->head;
    while (n) { dnode_t *nx = n->next; free(n); n = nx; }  /* drop any still held (not emitted) */

    pthread_cond_destroy(&dl->cv);
    pthread_mutex_destroy(&dl->lock);
    free(dl);
}
