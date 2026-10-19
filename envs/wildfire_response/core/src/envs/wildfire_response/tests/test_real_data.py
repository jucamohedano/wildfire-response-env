import unittest

from envs.wildfire_response.data.collect import catalog, facts_as_of, normalize


class EvidenceTests(unittest.TestCase):
    def test_catalog_provinces_and_dates(self):
        seed = catalog()
        self.assertEqual({case['province'] for case in seed['incidents']}, {'Almería','Huelva','Madrid'})
        for case in seed['incidents']:
            for fact in case['facts']:
                source = seed['sources'][fact['source']]
                self.assertTrue(source['published_date'].startswith('2026-'))
                self.assertTrue(source['url'].startswith('https://'))

    def test_retrospective_report_cannot_enter_early_agent_context(self):
        seed = catalog()
        case = seed['incidents'][0]
        self.assertEqual(facts_as_of(case, seed['sources'], '2026-07-09T17:00:00+02:00'), [])
        self.assertEqual(facts_as_of(case, seed['sources'], '2026-07-13T12:00:00+02:00'), [])
        self.assertEqual(len(facts_as_of(case, seed['sources'], '2026-07-14T00:00:00+02:00')), len(case['facts']))
        with self.assertRaises(ValueError):
            facts_as_of(case, seed['sources'], '2026-07-14T00:00:00')

    def test_madrid_filters_other_provinces_and_labels_geometry(self):
        case = catalog()['incidents'][2]
        areas = [dict(number=number, name=name, extent='POLYGON ((-4 40, -3 40, -3 41, -4 40))', products=[])
                 for number, name in [(1,'Brieva'),(2,'Villa del Prado'),(5,'San Martin de Valdeiglesias'),(6,'Navas del Rey')]]
        response = dict(results=[dict(code='EMSR900', name='Central Spain', countries=[dict(name='Spain')], category='Wildfire', eventTime='2026-07-20T01:07:00', aois=areas)])
        metadata, features, _ = normalize(case, response)
        self.assertEqual([area['number'] for area in metadata['areas']], [2,5,6])
        self.assertTrue(all(feature['properties']['geometry_role'] == 'mapping_coverage_not_fire_perimeter' for feature in features))
        response['results'][0]['eventTime'] = '2025-07-20T01:07:00'
        with self.assertRaises(ValueError):
            normalize(case, response)


if __name__ == '__main__':
    unittest.main()
