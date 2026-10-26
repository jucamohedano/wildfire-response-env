import copy
import json
import unittest
from unittest.mock import patch

from wildfire_core.benchmark_v1 import BenchmarkEnv, recompute_outcomes, verify_benchmark_trajectory
from wildfire_core.compact_ops_model import public_context
from envs.wildfire_response.regional.benchmark_v1.catalog import build_catalog, task_ids
from envs.wildfire_response.regional.benchmark_v1.observations import DISTRICT_DYNAMIC, RESOURCE_DYNAMIC
from envs.wildfire_response.regional.benchmark_v1.outcomes import score_outcomes, verify_outcomes
from envs.wildfire_response.tests.test_topographic_development import fixture_domain, fixture_inputs


def create(*, profile='central', ticks_per_action=2, max_decisions=None):
    prepared = fixture_inputs()
    domain = fixture_domain(prepared)
    return BenchmarkEnv(prepared, domain, expected_prepared_sha256=prepared['sha256'],
        expected_topographic_sha256=domain['sha256'], profile=profile,
        max_decisions=120 // ticks_per_action if max_decisions is None else max_decisions,
        ticks_per_action=ticks_per_action)


def case(environment, family='false_alarm'):
    group = next(group for group in environment.catalog['groups'] if group['family'] == family)
    return group['cases'][0]['id']


def finish(environment):
    while True:
        result = environment.step({})
        if result[2] or result[3]:
            return result


def outcome_records(*, exposed=0, burned=0, population=100, status='unwarned', active=False):
    initial = dict(minute=0, burning_cells=[[0, 0]], burned_cells=[[0, 0]],
        burnable_cells=[[column, 0] for column in range(11)], cell_area_hectares=25.0,
        population={'district': dict(population=population, exposed=0, status='unwarned')})
    final = dict(minute=1, burning_cells=[[0, 0]] if active else [],
        burned_cells=[[column, 0] for column in range(burned + 1)],
        population={'district': dict(population=population, exposed=exposed, status=status)})
    return [initial, final]


class BenchmarkOutcomeTests(unittest.TestCase):
    def test_graded_tradeoff_and_separate_safety_first_ranking(self):
        safe_records = outcome_records(burned=10)
        unsafe_records = outcome_records(exposed=1, burned=0)
        safe = recompute_outcomes(safe_records)
        unsafe = recompute_outcomes(unsafe_records)
        self.assertAlmostEqual(safe['graded_score'], .8)
        self.assertAlmostEqual(unsafe['graded_score'], .992)
        self.assertGreater(safe['ranking_key'], unsafe['ranking_key'])
        self.assertTrue(safe['safe_success'])
        self.assertFalse(unsafe['safe_success'])
        active = recompute_outcomes(outcome_records(active=True))
        self.assertTrue(active['safe_success'])
        self.assertEqual(active['graded_score'], 1)
        self.assertTrue(active['active_fire'])
        moving = recompute_outcomes(outcome_records(status='blocked'))
        self.assertFalse(moving['safe_success'])
        self.assertEqual(moving['unresolved_evacuations'], 1)

    def test_unique_footprint_initial_fire_area_and_no_population(self):
        records = outcome_records(burned=2, population=0)
        records.append(dict(copy.deepcopy(records[-1]), minute=2))
        records[-1]['burned_cells'].append([1, 0])
        actual = recompute_outcomes(records)
        self.assertEqual(actual['initial_burned_hectares'], 25)
        self.assertEqual(actual['additional_burned_hectares'], 50)
        self.assertEqual(actual['graded_score'], .8)
        self.assertEqual(actual['protected_population_fraction'], 1)
        footprint = {tuple(point) for record in records for point in record['burned_cells']}
        verify_outcomes(score_outcomes(records[0], records[-1], footprint, terminal=True), actual)

    def test_independent_recomputation_and_diagnostic_eligibility(self):
        records = outcome_records(exposed=20, burned=3)
        with patch('envs.wildfire_response.regional.benchmark_v1.outcomes.score_outcomes',
                   side_effect=AssertionError('Production score must not be called')):
            actual = recompute_outcomes(records)
        self.assertAlmostEqual(actual['graded_score'], .78)
        censored = recompute_outcomes(records, boundary_censored=True)
        self.assertEqual(censored['graded_score'], 0)
        self.assertFalse(censored['score_eligible'])
        self.assertFalse(censored['suitable_for_training'])
        abandoned = recompute_outcomes(records, abandoned=True)
        self.assertEqual(abandoned['graded_score'], 0)
        self.assertTrue(abandoned['score_eligible'])
        self.assertLess(abandoned['ranking_key'], actual['ranking_key'])
        with self.assertRaises(ValueError):
            recompute_outcomes([records[-1]])


class BenchmarkCoreTests(unittest.TestCase):
    def test_invalid_horizons_and_unsupported_limits_fail_before_catalog_construction(self):
        invalid = ((60, 1), (59, 2), (61, 2), (120, 2), (0, 2), (-60, -2),
                   (1, 120), (240, .5), (True, 120), (60.0, 2), (60, 2.0), (120, True))
        with patch('envs.wildfire_response.regional.benchmark_v1.catalog.build_catalog',
                   side_effect=AssertionError('Invalid limits must fail before building the catalog')):
            for max_decisions, ticks_per_action in invalid:
                with self.subTest(max_decisions=max_decisions, ticks_per_action=ticks_per_action):
                    with self.assertRaisesRegex(ValueError, '120-minute horizon'):
                        create(max_decisions=max_decisions, ticks_per_action=ticks_per_action)

    def test_per_minute_diagnostic_preserves_full_horizon_and_exact_replay(self):
        environment = create(max_decisions=120, ticks_per_action=1)
        initial, _ = environment.reset(case(environment))
        self.assertEqual(initial['clock']['decision_minutes'], 1)
        self.assertEqual(initial['clock']['horizon_minutes'], 120)
        for minute in range(1, 120):
            observation, reward, terminated, truncated, _ = environment.step({})
            self.assertEqual(observation['minute'], minute)
            self.assertEqual(observation['decision'], minute)
            self.assertEqual(reward, 0)
            self.assertFalse(terminated or truncated)
        observation, reward, terminated, truncated, _ = environment.step({})
        self.assertEqual((observation['minute'], observation['decision']), (120, 120))
        self.assertEqual(reward, 1)
        self.assertTrue(terminated)
        self.assertFalse(truncated)
        trajectory = environment.trajectory()
        self.assertEqual((trajectory['max_decisions'], trajectory['ticks_per_action']), (120, 1))
        self.assertEqual(len(trajectory['transitions']), 120)
        self.assertEqual(len(environment.private_audit()['records']), 121)
        self.assertTrue(environment.verify_episode()['passed'])
        with self.assertRaises(RuntimeError):
            environment.step({})

    def test_initial_unknowns_and_compact_adapter_compatibility(self):
        environment = create()
        observation, info = environment.reset()
        self.assertEqual(info['objective_score'], 0)
        for group in observation['districts']:
            self.assertTrue(all(group[key] is None for key in DISTRICT_DYNAMIC))
            self.assertIsNone(group['observed_at'])
            self.assertIsNone(group['received_at'])
            self.assertGreater(group['population'], 0)
        for unit in observation['resources']:
            self.assertTrue(all(unit[key] is None for key in RESOURCE_DYNAMIC))
            self.assertIsNone(unit['observed_at'])
        self.assertEqual(observation['pending_deliveries'], [])
        self.assertEqual(observation['sensor_memory'], [])
        self.assertNotIn('records', environment.trajectory())
        compact = public_context(observation)
        self.assertEqual(compact['districts'][0]['exposed'], None)

    def test_private_future_and_live_smoke_exposure_cannot_change_reads(self):
        environment = create()
        environment.reset()
        before = environment.get_situation()
        group = next(iter(environment._core.sim.groups.values()))
        group.update(burnt=group['count'], status='burnt', x=1, y=1)
        identifier = next(iter(environment._core.sim.groups))
        environment._core.sim._dose[identifier] = 10000
        environment._core.sim._smoke_present[identifier] = True
        environment._core.resources[next(iter(environment._core.resources))]['state'] = 'on_scene'
        environment._core.events.append(dict(tick=80, kind='ignition', location=[3, 3]))
        environment._core.queue.append(dict(kind='incident_update', due=90, data={'secret': 'HIDDEN'}))
        self.assertEqual(environment.get_situation(), before)
        self.assertEqual(environment.get_reports(), before['reports'])
        self.assertNotIn('HIDDEN', json.dumps(environment.trajectory()))

    def test_population_and_resource_snapshots_arrive_only_after_their_delay(self):
        environment = create(ticks_per_action=1)
        initial, _ = environment.reset()
        first, *_ = environment.step({})
        self.assertIsNone(first['districts'][0]['exposed'])
        self.assertEqual(first['resources'][0]['observed_at'], 0)
        self.assertEqual(first['resources'][0]['received_at'], 1)
        second, *_ = environment.step({})
        self.assertEqual(second['districts'][0]['observed_at'], 0)
        self.assertEqual(second['districts'][0]['received_at'], 2)
        self.assertEqual(second['districts'][0]['exposed'], 0)
        third, *_ = environment.step({})
        self.assertEqual(third['districts'][0]['observed_at'], 1)
        self.assertEqual(initial['districts'][0]['exposed'], None)
        self.assertEqual(third['pending_deliveries'], [])

    def test_checks_and_validation_only_use_delivered_evidence(self):
        environment = create()
        observation, _ = environment.reset()
        identifier = observation['resources'][0]['id']
        release = {'releases': [identifier]}
        first = environment.check_orders(release)
        environment._core.resources[identifier].update(state='on_scene', target=[14, 18])
        environment._core.sim.groups['settlement-west'].update(status='safe', burnt=200)
        second = environment.check_orders(release)
        self.assertFalse(first['valid'])
        self.assertEqual(first['error'], second['error'])
        request = dict(requests=[dict(resource_id=identifier,
            sector_id=observation['sectors'][0]['id'], mission='recon')])
        self.assertTrue(environment.check_orders(request)['valid'])
        with self.assertRaises(ValueError):
            environment.check_orders({})
        self.assertEqual(environment.get_situation()['minute'], 0)
        self.assertEqual(environment.get_situation(), observation)

    def test_delayed_reports_event_queries_and_unknown_ids(self):
        environment = create(ticks_per_action=1)
        environment.reset(case(environment, 'conflicting_reports'))
        unseen = {report['id'] for report in environment._core.report_events}
        self.assertTrue(unseen)
        for identifier in unseen:
            with self.assertRaises(ValueError):
                environment.get_reports([identifier])
        self.assertEqual(environment.get_events(), [])
        with self.assertRaises(ValueError):
            environment.get_events(['incident-event-1'])
        for invalid in (0, 101, True):
            with self.assertRaises(ValueError):
                environment.get_reports(limit=invalid)
        for _ in range(10):
            observation, *_ = environment.step({})
            self.assertTrue(all(event['observed_at'] <= event['received_at'] <= observation['minute']
                                for event in environment.get_events()))
        self.assertEqual(environment.get_situation(), observation)

    def test_triage_cannot_use_current_private_sensor_memory(self):
        environment = create(ticks_per_action=1)
        observation, _ = environment.reset(case(environment))
        report = observation['reports'][0]
        point = tuple(report['location'])
        snapshot = next(item for item in environment._core.evidence.pending if item['kind'] == 'operations')
        snapshot['payload']['sensor_memory'].append(dict(x=point[0], y=point[1], burning=False,
            observed_at=0, source='delayed-test-lookout'))
        assessment = {'triage': [dict(report_ids=[report['id']], assessment='no_fire_observed')]}
        self.assertFalse(environment.check_orders(assessment)['valid'])
        environment._core.memory[point] = dict(x=point[0], y=point[1], burning=True,
                                               observed_at=0, source='private')
        self.assertFalse(environment.check_orders(assessment)['valid'])
        observation, *_ = environment.step({})
        environment._core.memory[point]['burning'] = True
        self.assertTrue(environment.check_orders(assessment)['valid'])
        _, reward, _, _, info = environment.step(assessment)
        self.assertIsNone(info['error'])
        self.assertEqual(reward, 0)

    def test_profile_hooks_are_real_isolated_and_reset_does_not_compound(self):
        central, faster, slower = create(), create(profile='faster_stronger'), create(profile='slower_weaker')
        identifier = case(central)
        for environment in (central, faster, slower):
            environment.reset(identifier)
        baseline = central._core.pack['assumptions']['suppression_probability']
        for kind in baseline:
            self.assertAlmostEqual(faster._core.pack['assumptions']['suppression_probability'][kind],
                                   min(1, baseline[kind] * 1.5))
            self.assertAlmostEqual(slower._core.pack['assumptions']['suppression_probability'][kind],
                                   baseline[kind] * .5)
        self.assertLess(faster._core._grounded['ground_transit_minutes'],
                        central._core._grounded['ground_transit_minutes'])
        self.assertGreater(slower._core._grounded['ground_transit_minutes'],
                           central._core._grounded['ground_transit_minutes'])
        expected = copy.deepcopy(faster._core.pack)
        faster.reset(identifier)
        self.assertEqual(faster._core.pack, expected)

    def test_paired_hidden_locations_have_identical_initial_actor_evidence(self):
        environment = create()
        identifiers = task_ids(environment.catalog, 'development')
        first, _ = environment.reset(identifiers[0])
        second, _ = environment.reset(identifiers[8])
        first.pop('task_id')
        second.pop('task_id')
        self.assertEqual(first, second)

    def test_dispatch_eta_readback_and_arrival_ack_are_delivered(self):
        environment = create(ticks_per_action=1)
        observation, _ = environment.reset(case(environment))
        resource = observation['resources'][0]['id']
        sector = observation['sectors'][0]['id']
        estimate = observation['travel_estimates'][resource][sector]
        observation, *_ = environment.step(dict(requests=[dict(resource_id=resource,
            sector_id=sector, mission='recon')]))
        self.assertEqual(observation['offers'], [])
        observation, *_ = environment.step({})
        offer, readback = observation['offers'][0], observation['readbacks'][0]
        self.assertEqual(offer['lead_time_minutes'], estimate)
        self.assertFalse(environment.check_orders({'authorizations': [offer['id']]})['valid'])
        observation, _, _, _, info = environment.step(dict(authorizations=[offer['id']],
            readback_confirmations=[readback['id']]))
        self.assertIsNone(info['error'])
        self.assertEqual(observation['resources'][0]['state'], 'reserved')
        observation, *_ = environment.step({})
        resource_view = observation['resources'][0]
        self.assertEqual(resource_view['state'], 'mobilizing')
        self.assertEqual(resource_view['observed_at'] + 1, resource_view['received_at'])
        due = resource_view['arrival_at']
        self.assertIsInstance(due, int)
        while environment._core.sim.tick < due:
            observation, *_ = environment.step({})
        self.assertFalse(any(ack['kind'] == 'resource_arrived' for ack in observation['acknowledgements']))
        observation, *_ = environment.step({})
        ack = next(ack for ack in observation['acknowledgements'] if ack['kind'] == 'resource_arrived')
        self.assertEqual((ack['observed_at'], ack['received_at']), (due, due + 1))
        finish(environment)
        self.assertTrue(environment.verify_episode()['passed'])

    def test_protection_execution_and_population_effect_are_independently_delayed(self):
        environment = create(ticks_per_action=1)
        observation, _ = environment.reset(case(environment))
        district = observation['districts'][0]['id']
        observation, *_ = environment.step(dict(protection_orders=[dict(recipient=district,
            action='shelter', channel='broadcast', resource_id=None, supersedes=None)]))
        self.assertEqual(observation['protection_orders'][0]['status'], 'pending')
        self.assertNotIn('due_at', observation['protection_orders'][0])
        while environment._core.protection.orders['protection-1']['executed_at'] is None:
            observation, *_ = environment.step({})
        executed = environment._core.protection.orders['protection-1']['executed_at']
        self.assertEqual(environment._core.sim.tick, executed)
        self.assertNotEqual(observation['protection_orders'][0]['status'], 'executed')
        self.assertFalse(observation['districts'][0]['shelter_active'])
        observation, *_ = environment.step({})
        self.assertEqual(observation['protection_orders'][0]['status'], 'executed')
        self.assertFalse(observation['districts'][0]['shelter_active'])
        observation, *_ = environment.step({})
        self.assertTrue(observation['districts'][0]['shelter_active'])
        self.assertEqual(observation['districts'][0]['observed_at'], executed)

    def test_stale_releases_and_in_person_orders_fail_later_without_invalidating(self):
        environment = create(ticks_per_action=1)
        observation, _ = environment.reset(case(environment))
        crew = next(unit for unit in environment._core.evidence.resources if unit['kind'] == 'crew')
        district = observation['districts'][0]
        crew.update(state='on_scene', target=district['home'][:], observed_at=0, received_at=0)
        environment._core.evidence.operations['sensor_memory'] = [dict(x=district['home'][0],
            y=district['home'][1], burning=False, observed_at=0, received_at=0)]
        order = dict(protection_orders=[dict(recipient=district['id'], action='shelter',
            channel='in_person', resource_id=crew['id'], supersedes=None)])
        self.assertTrue(environment.check_orders(order)['valid'])
        observation, _, _, _, info = environment.step(order)
        self.assertIsNone(info['error'])
        self.assertEqual(observation['protection_orders'][0]['status'], 'pending')
        for _ in range(4):
            observation, _, _, _, info = environment.step({})
            self.assertIsNone(info['error'])
        self.assertEqual(observation['protection_orders'][0]['status'], 'failed')
        self.assertFalse(environment._core.sim.population_state()[district['id']]['shelter_active'])
        known = next(unit for unit in environment._core.evidence.resources if unit['id'] == crew['id'])
        known.update(state='on_scene', target=district['home'][:])
        self.assertTrue(environment.check_orders({'releases': [crew['id']]})['valid'])
        observation, _, _, _, info = environment.step({'releases': [crew['id']]})
        self.assertIsNone(info['error'])
        self.assertTrue(any(ack['kind'] == 'release_unavailable' for ack in observation['acknowledgements']))

    def test_duplicate_retry_uses_known_failure_status(self):
        environment = create(ticks_per_action=1)
        observation, _ = environment.reset(case(environment, 'failed_protection'))
        district = observation['districts'][0]['id']
        order = dict(recipient=district, action='shelter', channel='broadcast', resource_id=None, supersedes=None)
        environment.step({'protection_orders': [order]})
        previous = environment._core.protection.orders['protection-1']
        previous['status'] = 'failed'
        self.assertEqual(environment.get_situation()['protection_orders'][0]['status'], 'pending')
        retry = dict(order, supersedes='protection-1')
        self.assertTrue(environment.check_orders({'protection_orders': [retry]})['valid'])
        observation, _, _, _, info = environment.step({'protection_orders': [retry]})
        self.assertIsNone(info['error'])
        duplicate = next(record for record in observation['protection_orders'] if record['id'] == 'protection-2')
        self.assertEqual(duplicate['status'], 'duplicate_ignored')
        self.assertFalse(any(item['kind'] == 'protection_delivery'
                             and item['data']['order_id'] == 'protection-2' for item in environment._core.queue))

    def test_zero_population_engine_and_unique_burned_union(self):
        environment = create()
        environment.reset()
        core = environment._core
        for group in core.sim.groups.values():
            group['count'] = 0
        for group in core.records[0]['population'].values():
            group['population'] = 0
        initial = set(map(tuple, core.records[0]['burning_cells']))
        target = next(tuple(point) for point in core.records[0]['burnable_cells']
                      if tuple(point) not in initial and 8 < point[0] < 14 and 8 < point[1] < 14)
        core.sim.add_fire(*target)
        environment.step({})
        before = environment.outcomes()['additional_burned_cells']
        core._footprint()
        core._footprint()
        self.assertEqual(environment.outcomes()['additional_burned_cells'], before)
        self.assertIn(target, core.ever_burned)
        self.assertEqual(environment.outcomes()['protected_population_fraction'], 1)
        self.assertEqual(len(environment.private_audit()['records']), 3)

    def test_terminal_horizon_replay_reset_late_actions_and_tampering(self):
        environment = create()
        identifier = case(environment)
        initial, _ = environment.reset(identifier)
        for _ in range(12):
            _, reward, terminated, truncated, _ = environment.step({})
            self.assertEqual(reward, 0)
            self.assertFalse(terminated or truncated)
        final = finish(environment)
        self.assertEqual(final[0]['minute'], 120)
        self.assertTrue(final[2])
        self.assertFalse(final[3])
        self.assertEqual(final[1], 1)
        trace = json.loads(json.dumps(environment.trajectory()))
        verified = environment.verify_episode()
        self.assertTrue(verified['passed'])
        self.assertEqual(verified['objective_score'], environment.reward)
        self.assertEqual(verified['objective_result']['by_kind']['population_safety'], True)
        with self.assertRaises(RuntimeError):
            environment.step({})
        changed = copy.deepcopy(trace)
        changed['transitions'][0]['observation']['districts'][0]['exposed'] = 999
        with self.assertRaises(ValueError):
            verify_benchmark_trajectory(changed, environment._inputs, environment._domain)
        audit = environment.private_audit()
        audit['records'][-1]['population']['settlement-west']['exposed'] = 1
        with self.assertRaises(ValueError):
            verify_benchmark_trajectory(trace, environment._inputs, environment._domain, private_audit=audit)
        changed = copy.deepcopy(trace)
        changed['profile_sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            verify_benchmark_trajectory(changed, environment._inputs, environment._domain)
        reset, _ = environment.reset(identifier)
        self.assertEqual(reset, initial)

    def test_invalid_abandonment_and_boundary_censoring(self):
        environment = create()
        environment.reset(case(environment))
        for _ in range(3):
            observation, reward, terminated, truncated, info = environment.step({'invalid': True})
            self.assertIsNotNone(info['error'])
        self.assertFalse(terminated)
        self.assertTrue(truncated)
        self.assertEqual(reward, 0)
        self.assertTrue(environment.verify_episode()['passed'])
        self.assertTrue(environment.outcomes()['invalid_abandonment'])
        self.assertTrue(environment.outcomes()['score_eligible'])
        environment.reset(case(environment))
        environment._core.sim.add_fire(1, 10)
        _, reward, terminated, truncated, info = environment.step({})
        self.assertFalse(terminated)
        self.assertTrue(truncated)
        self.assertEqual(reward, 0)
        self.assertFalse(info['suitable_for_training'])
        self.assertFalse(info['outcomes']['score_eligible'])

    def test_catalog_pin_and_seed_enforced(self):
        environment = create()
        identifier = task_ids(environment.catalog, 'train')[0]
        with self.assertRaises(ValueError):
            environment.reset(identifier, seed=1)
        prepared, domain = fixture_inputs(), fixture_domain()
        catalog = build_catalog(prepared, domain)
        with self.assertRaises(ValueError):
            BenchmarkEnv(prepared, domain, expected_prepared_sha256=prepared['sha256'],
                expected_topographic_sha256=domain['sha256'], catalog=catalog,
                expected_catalog_sha256='0' * 64)


if __name__ == '__main__':
    unittest.main()
