import unittest

from envs.wildfire_response.data.discover import candidates, trusted_url, validate_layer


class DiscoveryTests(unittest.TestCase):
    def test_filters_year_country_category_and_cutoff(self):
        sample = dict(code='EMSR900', countries=['Spain'], category='Wildfire', eventTime='2026-07-20T01:07:00')
        entries = [sample, dict(sample, code='EMSR901', countries=['Portugal']),
                   dict(sample, code='EMSR902', eventTime='2025-07-20T01:07:00'),
                   dict(sample, code='EMSR903', eventTime='2026-11-20T01:07:00'),
                   dict(sample, code='EMSR904', category='Flood')]
        self.assertEqual(candidates(entries, '2026-10-18'), [sample])
        self.assertEqual(candidates([sample, sample], '2026-10-18'), [sample])
        with self.assertRaises(ValueError):
            candidates([sample, dict(sample, name='changed')], '2026-10-18')

    def test_untrusted_download_urls_are_rejected(self):
        trusted_url('https://rapidmapping.emergency.copernicus.eu/backend/')
        for url in ['http://rapidmapping.emergency.copernicus.eu/', 'https://example.org/',
                    'https://rapidmapping.emergency.copernicus.eu.evil.org/', 'file:///etc/passwd']:
            with self.assertRaises(ValueError):
                trusted_url(url)

    def test_coverage_and_nonfire_layers_are_not_fire_perimeters(self):
        feature = dict(type='Feature', properties=dict(event_type='8-Wildfire'),
                       geometry=dict(type='Polygon', coordinates=[[[-4, 40], [-3, 40], [-3, 41], [-4, 40]]]))
        data = dict(type='FeatureCollection', features=[feature])
        self.assertEqual(validate_layer(data), 1)
        feature['properties']['event_type'] = 'Flood'
        with self.assertRaises(ValueError):
            validate_layer(data)
        feature['properties']['event_type'] = '8-Wildfire'
        feature['geometry']['coordinates'] = []
        with self.assertRaises(ValueError):
            validate_layer(data)
        feature['properties']['event_type'] = '8-Wildfire'
        data['crs'] = dict(properties=dict(name='EPSG:3857'))
        with self.assertRaises(ValueError):
            validate_layer(data)


if __name__ == '__main__':
    unittest.main()
