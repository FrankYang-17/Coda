"""Optional P helpers; general collision, cloth and fluid solvers remain scene-authored.

Outputs are evidence-bearing samples, not an assertion of physical correctness.
Import this file in Blender via importlib.util.spec_from_file_location.
"""

import math


def vector(value):
    if len(value) != 3 or not all((math.isfinite(float(v)) for v in value)):
        raise ValueError("Expected three finite coordinates")
    return tuple((float(v) for v in value))


def free_flight(position, velocity, times, gravity=(0, 0, -9.81)):
    """Constant-gravity free flight; times are seconds since release."""
    (x, v, g) = (vector(position), vector(velocity), vector(gravity))
    if (
        not times
        or any((not math.isfinite(t) or t < 0 for t in times))
        or any((b <= a for (a, b) in zip(times, times[1:])))
    ):
        raise ValueError("Use increasing finite nonnegative times")
    return [
        {
            "time": t,
            "position": [x[i] + v[i] * t + 0.5 * g[i] * t * t for i in range(3)],
            "velocity": [v[i] + g[i] * t for i in range(3)],
            "driver": "constant_gravity_v1",
        }
        for t in times
    ]


def constrain_support(samples, height, radius=0.0):
    """P static-plane support, zero restitution: clamp penetration and inward normal velocity.

    For a horizontal static support plane only. No friction, bounce, arbitrary
    mesh collision, cloth/fluid simulation, or continuous collision-time claim.
    The actor must start above the surface. Keep
    samples for audit; this postprocess does not advance a general contact solver.
    """
    if not math.isfinite(height) or not math.isfinite(radius) or radius < 0 or (not samples):
        raise ValueError("Invalid support parameters")
    floor = height + radius
    if vector(samples[0]["position"])[2] < floor - 1e-08:
        raise ValueError("Initial penetration must be resolved explicitly")
    result = []
    for row in samples:
        (x, v) = (list(vector(row["position"])), list(vector(row["velocity"])))
        corrected = x[2] < floor or (x[2] <= floor and v[2] < 0)
        if corrected:
            (x[2], v[2]) = (max(x[2], floor), max(v[2], 0.0))
        result.append(
            {
                **row,
                "position": x,
                "velocity": v,
                "contact_corrected": corrected,
                "contact_driver": "static_horizontal_support_v1",
            }
        )
    return result
