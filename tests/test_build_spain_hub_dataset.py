import json
from pathlib import Path
import tempfile
import hashlib
import copy
from contextlib import ExitStack
from unittest.mock import patch
from unittest import TestCase
from eval.build_spain_hub_dataset import no_private_fields, write_json


class SpainPayloadPrivacyTests(TestCase):
    def test_nested_private_runtime_fields_are_rejected_before_file_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'payload.json'
            with self.assertRaisesRegex(ValueError,'Private'):
                write_json(path, {'records':[{'future_events':[{'minute':10}]}]})
            self.assertFalse(path.exists())

    def test_static_fuel_estimate_provenance_is_not_an_episode_label(self):
        value={'confidence':'low','source_known_mask':[[0]],'support_neighbours':[{'row':0,'column':1}],
               'description':'No future_events or private audits are included'}
        no_private_fields(value)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'source.json';write_json(path,value)
            self.assertEqual(json.loads(path.read_text()),value)
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()),['source.json'])


class HistoricalSnapshotPinTests(TestCase):
    def fixture(self, root):
        from eval import build_spain_hub_dataset as build
        rows={'incident_claims.jsonl':b'{"claim":"source"}\n',
              'municipal_population_2021.csv':b'population\n7\n','source_registry.json':b'{}\n'}
        outputs=[{'path':'data/'+name,'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in rows.items()]
        rows['provenance.json']=json.dumps({'outputs':outputs}).encode()
        for name,raw in rows.items():(root/name).write_bytes(raw)
        return build,{name:hashlib.sha256(raw).hexdigest() for name,raw in rows.items()}

    def test_reviewed_snapshot_passes_and_self_signed_changes_do_not(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);build,pins=self.fixture(root)
            with patch.object(build,'HISTORICAL_EVIDENCE_PINS',pins):
                build.validate_historical_evidence(root)
                (root/'incident_claims.jsonl').write_text('{"claim":"rewritten"}\n')
                value=json.loads((root/'provenance.json').read_text())
                value['outputs'][0]['sha256']=hashlib.sha256((root/'incident_claims.jsonl').read_bytes()).hexdigest()
                (root/'provenance.json').write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError,'immutable snapshot'):
                    build.validate_historical_evidence(root)

    def test_all_required_output_entries_are_mandatory(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);build,pins=self.fixture(root)
            value=json.loads((root/'provenance.json').read_text());value['outputs'].pop()
            raw=json.dumps(value).encode();(root/'provenance.json').write_bytes(raw)
            pins['provenance.json']=hashlib.sha256(raw).hexdigest()
            with patch.object(build,'HISTORICAL_EVIDENCE_PINS',pins):
                with self.assertRaisesRegex(ValueError,'required output'):
                    build.validate_historical_evidence(root)


class RuntimeMatrixTests(TestCase):
    def setUp(self):
        from eval import build_spain_hub_dataset as build
        self.build = build
        self.runtimes = {name: {'inputs_sha256': name+'-input', 'network_sha256': name+'-network'}
                         for name in ('fixture', 'real')}
        self.tasks = [{'runtime_id': name, 'task_id': f'{name}-{index}', 'scenario_sha256': f'{name}-{index}-sha'}
                      for name in self.runtimes for index in range(6)]
        expected = [dict(runtime_id=t['runtime_id'], case_id=t['task_id'], policy=policy)
                    for t in self.tasks for policy in ('passive', 'dispatcher', 'conservative', 'stress')]
        cells = [dict(item, status='complete', minute=1440, runtime_contradictions=0,
                      operation_coverage={'waits': {'status':'complete', 'observed':3, 'scope':'private detail'}},
                      durations={'waits': {'count':3, 'min_minutes':1, 'median_minutes':2, 'max_minutes':3}},
                      verification=dict(passed=True, exact_replay=True,
                      independent_reward_agreement=True), pins=dict(
                      inputs_sha256=self.runtimes[item['runtime_id']]['inputs_sha256'],
                      network_sha256=self.runtimes[item['runtime_id']]['network_sha256'],
                      scenario_sha256=next(t['scenario_sha256'] for t in self.tasks if t['task_id']==item['case_id'])))
                 for item in expected]
        self.report = dict(version='wildfire-runtime-matrix-v1', status='complete', implementation_sha256='implementation',
                           lifecycle_sha256='lifecycle', expected_cells=expected, cells=cells,
                           runtime_findings=[{'category':'action_coverage_gap'}, {'category':'unsupported_calibration'}])

    def check(self, report):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'report.json'
            path.write_text(json.dumps(report))
            return self.build.validation_excerpt(path, self.runtimes, self.tasks, 'lifecycle', 'implementation')

    def test_complete_matrix_exports_only_aggregate(self):
        self.report['cells'][0]['elapsed_seconds'] = 3
        result = self.check(self.report)
        self.assertEqual(result['completed_cells'], 48)
        self.assertEqual(result['runtime_findings'], {'action_coverage_gap':1, 'unsupported_calibration':1})
        self.assertNotIn('cells', result)
        self.assertNotIn('elapsed_seconds', json.dumps(result))

    def test_rejects_incomplete_or_untrusted_matrix(self):
        mutations = [
            lambda r: r['cells'].pop(),
            lambda r: r['cells'].append(copy.deepcopy(r['cells'][0])),
            lambda r: r['expected_cells'].pop(),
            lambda r: r['cells'][0].update(status='blocked'),
            lambda r: r['cells'][0].update(minute=1439),
            lambda r: r['cells'][0].update(runtime_contradictions=1),
            lambda r: r['cells'][0].update(durations=[{'start_minute':1}]),
            lambda r: r['cells'][0]['verification'].update(passed=1),
            lambda r: r['cells'][0]['verification'].update(exact_replay=False),
            lambda r: r['cells'][0]['pins'].update(scenario_sha256='wrong'),
            lambda r: r['cells'][0]['pins'].update(network_sha256='wrong'),
            lambda r: r.update(implementation_sha256='wrong'),
            lambda r: r.update(lifecycle_sha256='wrong'),
            lambda r: r.update(version='wrong'),
            lambda r: r.update(status='incomplete'),
            lambda r: r.pop('status'),
            lambda r: r.update(expected_cells=[x for x in r['expected_cells'] if x['policy'] != 'stress'],
                               cells=[x for x in r['cells'] if x['policy'] != 'stress']),
            lambda r: r['runtime_findings'].append({'category':'blocking'}),
            lambda r: r['runtime_findings'].append({'category':'contradiction'}),
            lambda r: r['runtime_findings'].append({'category':'error'}),
            lambda r: r['cells'][0].update(findings=[{'category':'contradiction'}]),
            lambda r: r['cells'][0].update(runtime_findings=[{'category':'blocking'}]),
            lambda r: r['cells'][0].update(findings=[{'category':'error'}]),
            lambda r: r['cells'][0].update(action_arguments={'x':1}),
            lambda r: r['cells'][0].update(checkpoint={'state':1}),
            lambda r: r['cells'][0].update(journal=[]),
            lambda r: r['cells'][0].update(metadata={'api_key':'secret'}),
            lambda r: r.update(expected_cells=[], cells=[]),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                report = copy.deepcopy(self.report)
                mutation(report)
                with self.assertRaises(ValueError): self.check(report)

    def test_non_blocking_findings_and_optional_validator_pins(self):
        self.report['cells'][0]['findings'] = [{'category':'unsupported'}, {'category':'review'},
                                               {'category':'not_exercised'}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for key, relative in self.build.VALIDATION_PIN_PATHS.items():
                path = root/relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(key)
                self.report[key] = hashlib.sha256(path.read_bytes()).hexdigest()
            with patch.object(self.build, 'ROOT', root):
                result = self.check(self.report)
                self.assertEqual(result['cell_findings'], {'unsupported':1, 'review':1, 'not_exercised':1})
                self.assertEqual(result['action_checker_sha256'], self.report['action_checker_sha256'])
                self.assertEqual(result['profile_sha256'], self.report['profile_sha256'])
                for key in self.build.VALIDATION_PIN_PATHS:
                    changed = copy.deepcopy(self.report)
                    changed[key] = '0'*64
                    with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                        self.check(changed)

    def test_combined_validator_digest_and_compact_review_rows(self):
        from eval.validate_incident_day_runtime import validator_digest
        self.report['validator_sha256'] = validator_digest()
        self.report['cells'][0]['findings'] = [{'category':'unsupported', 'detail':'do not export'}]
        self.report['cells'][0]['operation_coverage']['waits']['extra'] = 'do not export'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'report.json'; path.write_text(json.dumps(self.report))
            summary, rows = self.build.validation_excerpt(path, self.runtimes, self.tasks,
                'lifecycle', 'implementation', include_cells=True)
        self.assertEqual(summary['validator_sha256'], validator_digest())
        self.assertEqual(len(rows), 48)
        row = rows[0]
        self.assertEqual(row['minute'], 1440)
        self.assertEqual(json.loads(row['operation_coverage_json'])['waits'], {'status':'complete', 'observed':3})
        self.assertEqual(json.loads(row['durations_json'])['waits']['median_minutes'], 2)
        self.assertEqual(json.loads(row['findings_json']), {'unsupported':1})
        self.assertFalse(row['actor_input_eligible'])
        self.assertFalse(row['training_eligible'])
        self.assertNotIn('do not export', json.dumps(rows))
        with tempfile.TemporaryDirectory() as directory:
            import pyarrow.parquet as pq
            path = Path(directory)/'cells.parquet'
            self.build.table(path, rows)
            self.assertEqual(pq.read_table(path).to_pylist(), rows)
        self.report['validator_sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'validator_sha256'): self.check(self.report)

    def test_visual_review_is_pinned_and_preserves_caveats(self):
        path = self.build.ROOT/'data/incident_day_v2/fuel_review/visual_land_context_v1.json'
        review = self.build.visual_review_excerpt(path)
        self.assertEqual(review['summary']['centres_reviewed'], 27)
        self.assertIn('Assistant inspected', review['method'])
        self.assertTrue(review['limitations'])
        self.assertFalse(review['fuel_values_changed'])
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory)/'review.json'
            changed.write_bytes(path.read_bytes()+b' ')
            with self.assertRaisesRegex(ValueError, 'pinned source'):
                self.build.visual_review_excerpt(changed)

    def test_cli_forwards_options(self):
        import sys
        args = ['builder', '--output','out', '--real-inputs','inputs', '--network','roads',
                '--weather','weather', '--evidence','evidence', '--release-version','0.3.0',
                '--validation-report','report', '--visual-land-review','review']
        result = {'counts':{}, 'files':[]}
        with patch.object(sys, 'argv', args), patch.object(self.build, 'build', return_value=result) as build:
            self.build.main()
        self.assertEqual(build.call_args.kwargs['release_version'], '0.3.0')
        self.assertEqual(build.call_args.kwargs['validation_report'], 'report')
        self.assertEqual(build.call_args.kwargs['visual_land_review'], 'review')

    def test_build_manifest_default_and_optional_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root/'data/malaga_2021'; data.mkdir(parents=True)
            evidence = root/'evidence'; evidence.mkdir()
            weather = {'records':[{'observed_at_utc':str(i)} for i in range(384)],
                       'status':'source_clock_normalized', 'version':'v', 'station':{},
                       'role':'context', 'checks':{}, 'limitations':[]}
            weather_path = root/'weather.json'; weather_path.write_text(json.dumps(weather))
            (data/'weather_normalization_v1.json').write_text(json.dumps({'output_files':[
                {'path':'x/weather.json','sha256':hashlib.sha256(weather_path.read_bytes()).hexdigest()}]}))
            (data/'terrain_closeout_20261029.json').write_text(json.dumps({'retained_evidence':{}}))
            (evidence/'incident_claims.jsonl').write_text('{}\n'*7)
            (evidence/'municipal_population_2021.csv').write_text('population_total\n'+'7\n'*7)
            for name in ('source_registry.json','provenance.json'): (evidence/name).write_text('{}')
            inputs = {'sha256':'input', 'grid':{'width':1,'height':1,'cell_km':1,'crs':'x'}}
            roads = {'sha256':'network'}
            input_path=root/'input.json'; input_path.write_text(json.dumps(inputs))
            road_path=root/'roads.json'; road_path.write_text(json.dumps(roads))
            class Env:
                def __init__(self, *args, **kwargs): pass
                def reset(self, case):
                    self._scenario={'sha256':case+'-sha'}
                    return {}, {}
            with ExitStack() as stack:
                for name, value in {'ROOT':root, 'PACK_PIN':'input', 'NETWORK_PIN':'network',
                        '_check_hash':lambda x, _: x['sha256'], 'fixture_inputs':lambda: (inputs, roads),
                        'registry':lambda: {'sha256':'lifecycle'}, 'active_day_case_ids':lambda *args: [str(i) for i in range(6)],
                        'IncidentDayEnv':Env, 'implementation_digest':lambda: 'implementation',
                        'validate_historical_evidence':lambda _: None}.items():
                    stack.enter_context(patch.object(self.build, name, value))
                stack.enter_context(patch.object(self.build.subprocess, 'check_output', return_value='revision\n'))
                args = dict(real_inputs=input_path, network=road_path, weather=weather_path, evidence=evidence)
                default = self.build.build(root/'default', **args)
                self.assertEqual(default['release_version'], '0.2.0')
                self.assertEqual(default['dataset_card_status'], 'awaiting_owner_readme_consent')
                self.assertNotIn('validation_report', default)
                original_sources = json.loads((root/'default/provenance/sources.json').read_text())
                original_scope = next(source['scope'] for source in original_sources['sources'] if source['id'] == 'siose-context')
                self.assertIn('27 unresolved', original_scope)
                with self.assertRaisesRegex(ValueError, 'Invalid release version'):
                    self.build.build(root/'invalid', **args, release_version='0.3.0+build')
                self.assertFalse((root/'invalid').exists())
                stack.enter_context(patch.object(self.build, 'validation_excerpt', return_value=({'version':'matrix'},
                    [{'case_id':'test', 'actor_input_eligible':False, 'training_eligible':False}])))
                stack.enter_context(patch.object(self.build, 'visual_review_excerpt', return_value={'version':'visual'}))
                result = self.build.build(root/'new', **args, release_version='0.3.0',
                                          validation_report='report', visual_land_review='review')
                self.assertEqual(result['release_version'], '0.3.0')
                self.assertEqual(result['dataset_card_status'], 'owner_requested_private_release')
                sources = json.loads((root/'new/provenance/sources.json').read_text())
                scope = next(source['scope'] for source in sources['sources'] if source['id'] == 'siose-context')
                for phrase in ('27 centres unclassified', 'all 27 centres are on land', 'Whole-cell water fractions',
                               'historical fuel', 'censor guards are unchanged'):
                    self.assertIn(phrase, scope)
                self.assertEqual(result['tables']['validation_cells'], 'validation/cells.parquet')
                self.assertEqual(result['counts']['validation_cells'], 1)
                import pyarrow.parquet as pq
                self.assertEqual(pq.read_table(root/'new'/result['tables']['validation_cells']).to_pylist()[0]['case_id'], 'test')
                for key in ('validation_report', 'visual_land_review'):
                    self.assertTrue((root/'new'/result[key]).is_file())
                    self.assertIn(result[key], {item['path'] for item in result['files']})
