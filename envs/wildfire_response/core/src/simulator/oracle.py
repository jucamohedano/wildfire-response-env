"""Hindsight oracle.

Replays a frozen decision state with ground truth (hidden fire included) to grade
the decision HappyRobot took against a bounded set of alternatives. The result is
a regret figure and a gap type. The oracle grades; it never decides.

Cost weights and thresholds are configuration, kept in one place.
"""
import copy
import itertools
import math
import random
import time

# Per person: burnt, or still unwarned while in the downwind sector at the end of the
# horizon. A warning already en route counts half, so short horizons still reward the
# order that starts it. Cells and blocked districts are secondary terms.
WEIGHTS = dict(people_burnt=1000., unwarned_downwind=2., warning_en_route_factor=.5, blocked=20., burned=5., extinguished=-3.)
JUDGEMENT_THRESHOLD = 100.
SECTOR_COS = 0.7
NEAR_CELLS = 12
TOP_PER_VEHICLE = 2
RANK_HORIZON = 4


def _metrics(sim):
    return dict(burned=sum(c.get('burned', 0) > 0 for row in sim.cells for c in row),
                burnt=sum(g.get('burnt', 0) for g in sim.groups.values()),
                ext=sim.suppressed+sim.crew_extinguished)


def _downwind_urgency(sim, group):
    fires = sim.fire_points()
    sources = fires or ([tuple(sim.report)] if sim.called else [])
    strength = math.hypot(*sim.wind)
    for x, y in sources:
        dx, dy = group['x']-x, group['y']-y
        d = math.hypot(dx, dy)
        if d <= 8:
            return 1.
        if strength and d and (dx*sim.wind[0]+dy*sim.wind[1])/(d*strength) >= SECTOR_COS:
            return 1. if fires else .2
    return 0.


def _warning_en_route(sim, district):
    return any(v.get('evacuation_group') == district and str(v.get('mode', '')).startswith('evacuate_') and v.get('target')
               for v in sim.extinguishers+sim.scouts)


def cost(sim, before):
    after = _metrics(sim)
    unwarned = sum(g['count']*_downwind_urgency(sim, g)*(WEIGHTS['warning_en_route_factor'] if _warning_en_route(sim, k) else 1.)
                   for k, g in sim.groups.items() if g['status'] == 'unwarned')
    blocked = sum(g['status'] == 'blocked' for g in sim.groups.values())
    return round(WEIGHTS['people_burnt']*(after['burnt']-before['burnt'])
                 + WEIGHTS['unwarned_downwind']*unwarned
                 + WEIGHTS['blocked']*blocked
                 + WEIGHTS['burned']*(after['burned']-before['burned'])
                 + WEIGHTS['extinguished']*(after['ext']-before['ext']), 3)


def _order(drone, command, x, y, district=''):
    return dict(drone_id=drone['drone_id'], command=command, target_x=int(x), target_y=int(y), district_id=district, reason='oracle candidate')


def vehicle_options(sim):
    """Per-vehicle option lists; the first option of each list is the passive default."""
    sim = copy.deepcopy(sim)
    sim.observe()
    unwarned = [(k, g) for k, g in sim.groups.items() if g['status'] == 'unwarned']
    strength = math.hypot(*sim.wind)
    leading = sorted(sim.observation, key=lambda c: -(c['x']*sim.wind[0]+c['y']*sim.wind[1]))[:2] if strength else sim.observation[:2]
    per = []
    for d in sim.extinguishers:
        opts = [_order(d, 'hold', d['x'], d['y'])]
        opts += [_order(d, 'scout', p['x'], p['y']) for p in sim.smoke_scout_positions()[:3]]
        opts += [_order(d, 'contain', p['x'], p['y']) for p in sim.safe_drone_positions(d)[:3]]
        opts += [_order(d, 'evacuate_farm' if g['kind'] == 'farm' else 'evacuate_town', *g['home'], k) for k, g in unwarned]
        per.append(('extinguisher_orders', opts))
    for t in sim.trucks:
        opts = [dict(truck_id=t['truck_id'], command='continue', reason='oracle candidate')]
        targets = [(c['x'], c['y']) for c in leading] or ([tuple(sim.report)] if sim.called else [])
        opts += [dict(truck_id=t['truck_id'], command='attack_sector', target_x=int(x), target_y=int(y), reason='oracle candidate') for x, y in targets]
        per.append(('truck_orders', opts))
    for s in sim.scouts:
        rx, ry = sim.report if sim.called else sim.base
        ring = [[max(0, min(sim.width-1, x)), max(0, min(sim.height-1, y))] for x, y in ((rx-4, ry), (rx, ry+4), (rx+4, ry))]
        opts = [dict(drone_id=s['drone_id'], command='continue', waypoints=[], reason='oracle candidate'),
                dict(drone_id=s['drone_id'], command='patrol', waypoints=ring, reason='oracle candidate')]
        opts += [dict(drone_id=s['drone_id'], command='evacuate_farm' if g['kind'] == 'farm' else 'evacuate_town', district_id=k, waypoints=[], reason='oracle candidate')
                 for k, g in unwarned]
        per.append(('scout_orders', opts))
    return per


def assemble(parts):
    dec = dict(mission='oracle candidate', drone_reason='oracle candidate', extinguisher_orders=[], scout_orders=[], truck_orders=[])
    for key, order in parts:
        dec[key].append(copy.deepcopy(order))
    dec['primary_command'] = dec['extinguisher_orders'][0]['command'] if dec['extinguisher_orders'] else 'hold'
    return dec


def is_valid(sim, decision):
    try:
        s = copy.deepcopy(sim)
        s.apply(copy.deepcopy(decision), 'oracle-validate', s.incident_id, s.tick)
        return True
    except (ValueError, KeyError, TypeError, StopIteration, AttributeError):
        return False


def rollout(sim, decision, horizon, seed):
    s = copy.deepcopy(sim)
    s.rng = random.Random(seed)
    s.suppression_rng = random.Random(seed+1)
    before = _metrics(s)
    s.apply(copy.deepcopy(decision), f'oracle-{seed}', s.incident_id, s.tick)
    s.step(horizon)
    return cost(s, before)


def candidates(sim, max_joint=20, deadline=None):
    """Greedy: rank each vehicle's options with the others passive, then combine the top few."""
    per = vehicle_options(sim)
    if not per:
        return [], False
    defaults = [(key, opts[0]) for key, opts in per]
    truncated = False
    shortlisted = []
    for i, (key, opts) in enumerate(per):
        scored = []
        for opt in opts:
            parts = list(defaults)
            parts[i] = (key, opt)
            dec = assemble(parts)
            if not is_valid(sim, dec):
                continue
            if deadline and time.monotonic() > deadline:
                truncated = True
                scored.append((0., opt))
                continue
            scored.append((rollout(sim, dec, RANK_HORIZON, 9), opt))
        scored.sort(key=lambda t: t[0])
        shortlisted.append([(key, opt) for _, opt in scored[:TOP_PER_VEHICLE]] or [(key, opts[0])])
    out, seen = [], set()
    for parts in itertools.product(*shortlisted):
        dec = assemble(parts)
        sig = repr([(k, sorted(o.items())) for k in ('extinguisher_orders', 'scout_orders', 'truck_orders') for o in dec[k]])
        if sig in seen:
            continue
        seen.add(sig)
        if is_valid(sim, dec):
            out.append(dec)
        if len(out) >= max_joint:
            truncated = True
            break
    return out, truncated


def hidden_fire(sim):
    known = {(c['x'], c['y']) for c in sim.memory.values() if c['burning']}
    return [p for p in sim.fire_points() if p not in known]


def _needs_hidden(best, hidden, sim):
    """True when the best decision aims near hidden fire and away from any known fire."""
    known = [(c['x'], c['y']) for c in sim.memory.values() if c['burning']]
    targets = [(o.get('target_x'), o.get('target_y')) for k in ('extinguisher_orders', 'truck_orders')
               for o in best.get(k, []) if o.get('command') not in ('hold', 'continue')]
    targets += [tuple(sim.groups[o['district_id']]['home']) for o in best.get('scout_orders', []) if o.get('district_id') in sim.groups]
    for t in targets:
        if t[0] is None:
            continue
        near_hidden = min(math.dist(t, h) for h in hidden) <= NEAR_CELLS
        near_known = bool(known) and min(math.dist(t, k) for k in known) <= NEAR_CELLS
        if near_hidden and not near_known:
            return True
    return False


def evaluate(snapshot, actual, signals=None, horizon=16, seeds=(9,), max_joint=20, time_budget=10.0):
    start = time.monotonic()
    deadline = start+time_budget
    signals = signals or {}
    sim = copy.deepcopy(snapshot)
    hidden = hidden_fire(sim)
    actual_valid = is_valid(sim, actual)

    def score(dec):
        return round(sum(rollout(sim, dec, horizon, s) for s in seeds)/len(seeds), 3)

    actual_cost = score(actual) if actual_valid else None
    pool, truncated = candidates(sim, max_joint, deadline)
    results = []
    for dec in pool:
        if time.monotonic() > deadline:
            truncated = True
            break
        results.append((score(dec), dec))
    results.sort(key=lambda t: t[0])
    best_cost, best = results[0] if results else (actual_cost, actual)
    if actual_valid and best_cost is not None and actual_cost <= best_cost:
        best_cost, best = actual_cost, actual
    regret = round(actual_cost-best_cost, 3) if actual_valid and best_cost is not None else None
    rank = (1+sum(1 for c, _ in results if c < actual_cost)) if actual_valid else None
    if not actual_valid or signals.get('loop_detected') or not signals.get('terminated_cleanly', True):
        gap = 'execution'
    elif regret is None or regret <= JUDGEMENT_THRESHOLD:
        gap = 'none'
    elif hidden and _needs_hidden(best, hidden, sim):
        gap = 'information'
    else:
        gap = 'judgement'
    return dict(actual_cost=actual_cost, best_cost=best_cost, regret=regret, actual_rank=rank,
                candidate_count=len(results)+int(actual_valid), best_decision=best, actual_valid=actual_valid,
                gap_type=gap, hidden_fire_at_decision=[list(p) for p in hidden[:20]], truncated=truncated,
                weights=WEIGHTS, horizon=horizon, seeds=list(seeds), elapsed_s=round(time.monotonic()-start, 2))
