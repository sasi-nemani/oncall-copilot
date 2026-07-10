#!/usr/bin/env python3
"""How brute-force (exact) vector search scales, and where you'd switch to an ANN index.

A brute-force query compares the question vector against EVERY stored vector: cost is O(N x dim) per
query — it grows linearly with the corpus. An ANN index (Vertex tree-AH/ScaNN, HNSW, IVF) searches a
sublinear structure and returns in near-constant time, at the cost of a little recall. So the decision
"managed ANN vs a for-loop" is really: at your N, is brute-force still under your latency budget?

This measures brute-force query latency at increasing N for 384-dim vectors (our embedding size),
so the crossover is a number, not a hunch. Run:  python scripts/bruteforce_scaling.py
"""
import time
import numpy as np

DIM = 384                       # all-MiniLM-L6-v2 output dim (what the app uses)
K = 10                          # neighbours per query
SIZES = [1_000, 10_000, 100_000, 500_000]
QUERIES = 50                    # queries to average each timing over
BUDGETS = [10, 50, 100]         # latency budgets (ms) to report the crossover against

rng = np.random.default_rng(0)


def _bruteforce_ms(mat, q):
    # exactly what a brute-force index does: score all, take top-K.
    t0 = time.perf_counter()
    sims = mat @ q               # (N, dim) @ (dim,) -> (N,)  cosine (vectors are normalized)
    np.argpartition(-sims, K)[:K]
    return (time.perf_counter() - t0) * 1000


def main():
    print(f"Brute-force exact search — {DIM}-dim vectors, top-{K}, avg of {QUERIES} queries\n")
    print(f"{'N vectors':>12} | {'RAM (MB)':>9} | {'ms/query':>9} | {'queries/sec':>11}")
    print("-" * 52)
    rows = []
    for n in SIZES:
        mat = rng.standard_normal((n, DIM)).astype(np.float32)
        mat /= np.linalg.norm(mat, axis=1, keepdims=True)     # normalize -> dot == cosine
        qs = rng.standard_normal((QUERIES, DIM)).astype(np.float32)
        qs /= np.linalg.norm(qs, axis=1, keepdims=True)
        ms = np.median([_bruteforce_ms(mat, qs[i]) for i in range(QUERIES)])
        ram = mat.nbytes / 1e6
        rows.append((n, ms))
        print(f"{n:>12,} | {ram:>9.0f} | {ms:>9.2f} | {1000 / ms:>11,.0f}")
        del mat

    # Linear extrapolation (brute-force is O(N)) to the latency budgets — the "switch to ANN" line.
    n0, ms0 = rows[-1]
    per_vec_ms = ms0 / n0
    print(f"\nLinear fit: ~{per_vec_ms * 1e6:.2f} ms per 1M vectors (brute-force is O(N)).")
    print("Crossover — N at which brute-force exceeds a p50 latency budget (single thread, this box):")
    for b in BUDGETS:
        print(f"  > {b:>3} ms  at  ~{b / per_vec_ms:>14,.0f} vectors")
    print("\nReading it: below the crossover, an exact for-loop is simplest and gives 100% recall.")
    print("Above it, you want an ANN index (Vertex tree-AH/ScaNN, HNSW) — near-constant latency, ~95-99%")
    print("recall. Vertex's tree-AH won't even build below a few thousand vectors, which is the same")
    print("message from the other direction: managed ANN is a large-N tool.")


if __name__ == "__main__":
    main()
