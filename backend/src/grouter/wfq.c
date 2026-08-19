/*
 * wfq.c -- REMOVED.
 *
 * The worst-case weighted fair queuing scheduler that used to live here was never
 * wired into the packet core (PktCoreSchedulerInit always ran the round-robin
 * scheduler) and carried long-standing "TODO: Debug" bugs. It has been replaced by
 * a Deficit Round Robin (DRR) scheduler in roundrobin.c, selectable at runtime with
 * `spolicy set rr|drr`. This file is intentionally empty; it is no longer in the
 * Makefile SOURCES. It can be deleted from the tree with `git rm src/grouter/wfq.c`.
 */
