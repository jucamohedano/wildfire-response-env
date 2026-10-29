"""Build prepared Spain development data locally; never uploads or writes a README."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pyarrow as pa
import pyarrow.parquet as pq

from wildfire_core.catalog_lifecycle import registry, active_day_case_ids
from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs
from envs.wildfire_response.incident_day.environment import implementation_digest
from envs.wildfire_response.incident_day.data import _check_hash
from envs.wildfire_response.incident_day.real_map_scenarios import PACK_PIN, NETWORK_PIN

VERSION = 'wildfire-response-spain-runtime-v1'
REPO_ID = 'jucamohedano/wildfire-response-spain'
ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_EVIDENCE_PINS = {
    'provenance.json': 'bd8da1253349748b15975e94334947ad28807b0b8b91beaefb4e31767971965b',
    'incident_claims.jsonl': 'c6d4840523bc43f02cd9e6475d4e5584cb6935c497b544f5a3069b5ca2b95c13',
    'municipal_population_2021.csv': 'd490bd51b3ef4894e2e92c075d3bd5e29afc8acc0ff60e6d97e497dded077ebb',
    'source_registry.json': 'a10ad79c735952b5ae8097907a2ccb4882e08172262f96875830594761114951',
}
FORBIDDEN_KEYS = {'authorization', 'cookie', 'set-cookie', 'access_token', 'api_key', 'password',
                  'private_audit', 'evaluator_state', 'checkpoint', 'future_events', 'scenario_factors',
                  'truth_labels', 'generation_annotations'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def no_private_fields(value):
    if isinstance(value, dict):
        if FORBIDDEN_KEYS.intersection(str(k).lower() for k in value):
            raise ValueError('Private or secret field refused in data asset')
        for val in value.values(): no_private_fields(val)
    elif isinstance(value, list):
        for val in value: no_private_fields(val)


def write_json(path, value):
    no_private_fields(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value) + b'\n')


def table(path, rows, schema=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path, compression='zstd')


def validate_historical_evidence(evidence):
    evidence = Path(evidence)
    for name, expected in HISTORICAL_EVIDENCE_PINS.items():
        if hashlib.sha256((evidence/name).read_bytes()).hexdigest() != expected:
            raise ValueError('Historical evidence differs from reviewed immutable snapshot')
    provenance = json.loads((evidence/'provenance.json').read_text())
    found = {}
    for item in provenance['outputs']:
        name = Path(item['path']).name
        if name in HISTORICAL_EVIDENCE_PINS and name != 'provenance.json':
            if name in found or item['sha256'] != HISTORICAL_EVIDENCE_PINS[name]:
                raise ValueError('Historical evidence output pin differs')
            found[name] = item
    if set(found) != set(HISTORICAL_EVIDENCE_PINS) - {'provenance.json'}:
        raise ValueError('Historical evidence required output entries are missing')
    return provenance


def build(output, *, real_inputs, network, weather, evidence):
    output, evidence = Path(output), Path(evidence)
    if output.exists(): raise ValueError('Use a fresh output directory')
    source = json.loads(Path(real_inputs).read_text())
    roads = json.loads(Path(network).read_text())
    if _check_hash(source, 'real input') != PACK_PIN or _check_hash(roads, 'road graph') != NETWORK_PIN:
        raise ValueError('Prepared runtime pins do not match current active source-map suite')
    weather_path = Path(weather)
    weather_pin = next(item['sha256'] for item in json.loads((ROOT/'data/malaga_2021/weather_normalization_v1.json').read_text())['output_files'] if item['path'].endswith('/weather.json'))
    if hashlib.sha256(weather_path.read_bytes()).hexdigest() != weather_pin:
        raise ValueError('Normalized weather differs from recorded source hash')
    weather = json.loads(weather_path.read_text())
    validate_historical_evidence(evidence)
    if len(weather['records']) != 384 or weather['status'] != 'source_clock_normalized':
        raise ValueError('Use the verified 384-row normalized weather document')
    fixture, fixture_roads = fixture_inputs()
    lifecycle = registry()
    output.mkdir(parents=True)
    runtimes = {}
    tasks, landscapes = [], []
    for identifier, inputs, graph in (('fixture', fixture, fixture_roads), ('es-ma-2021', source, roads)):
        directory = output/'runtime'/identifier
        write_json(directory/'inputs.json', inputs)
        write_json(directory/'network.json', graph)
        ids = active_day_case_ids(inputs['sha256'], graph['sha256'])
        unapproved = identifier != 'fixture'
        runtimes[identifier] = dict(inputs=f'runtime/{identifier}/inputs.json',
            network=f'runtime/{identifier}/network.json', inputs_sha256=inputs['sha256'],
            network_sha256=graph['sha256'], allow_unapproved_data=unapproved, scenario_ids=list(ids))
        env = IncidentDayEnv(inputs, graph, expected_inputs_sha256=inputs['sha256'],
            expected_network_sha256=graph['sha256'], allow_unapproved_data=unapproved)
        for index, case in enumerate(ids):
            observation, _ = env.reset(case)
            tasks.append(dict(task_id=case, runtime_id=identifier, split='development',
                related_exercise_group=f'day-development-{index:03}',
                scenario_sha256=env._scenario['sha256'], horizon_minutes=1440,
                inputs_ref=runtimes[identifier]['inputs'], network_ref=runtimes[identifier]['network'],
                synthetic=True, historical_replay=False, training_eligible=False))
        landscapes.append(dict(runtime_id=identifier, region_id='synthetic-fixture' if identifier=='fixture' else 'es-ma-2021',
            width_cells=inputs['grid']['width'], height_cells=inputs['grid']['height'],
            cell_metres=inputs['grid']['cell_km']*1000, crs=inputs['grid']['crs'],
            inputs_ref=runtimes[identifier]['inputs'], inputs_sha256=inputs['sha256'],
            network_ref=runtimes[identifier]['network'], network_sha256=graph['sha256'],
            synthetic_geography=identifier=='fixture', real_data_approved=False))
    table(output/'tasks/development.parquet', tasks)
    table(output/'landscapes/records.parquet', landscapes)
    fields = [('station_id',pa.string()),('source_row',pa.int64()),('source_date',pa.string()),('source_hour',pa.string()),
        ('source_timestamp_utc',pa.string()),('temperature_c',pa.float64()),('relative_humidity_percent',pa.float64()),
        ('wind_speed_m_s',pa.float64()),('wind_direction_degrees',pa.float64()),('precipitation_mm',pa.float64()),
        ('received_at_utc',pa.string()),('actor_input_eligible',pa.bool_()),('measurement_time_semantics',pa.string())]
    rows=[]
    for row in weather['records']:
        value={key:row.get(key) for key,_ in fields}
        value.update(station_id='MA04', source_timestamp_utc=row['observed_at_utc'], received_at_utc=None,
                     actor_input_eligible=False)
        rows.append(value)
    if len({r['source_timestamp_utc'] for r in rows})!=384: raise ValueError('Weather timestamps must be unique')
    table(output/'weather/records.parquet', rows, pa.schema(fields))
    claims=[json.loads(line) for line in (evidence/'incident_claims.jsonl').read_text().splitlines() if line.strip()]
    if len(claims)!=7: raise ValueError('Historical claims snapshot count differs')
    table(output/'incident_claims/records.parquet',[dict(record_json=json.dumps(row,sort_keys=True),
        actor_input_eligible=False, source_snapshot='spain-sierra-bermeja-2021-v0.1.0') for row in claims])
    with (evidence/'municipal_population_2021.csv').open() as stream: population=list(csv.DictReader(stream))
    if len(population)!=7: raise ValueError('Population snapshot count differs')
    for row in population: row['population_total'] = int(row['population_total'])
    table(output/'municipal_population/records.parquet',population)
    write_json(output/'provenance/lifecycle.json',lifecycle)
    for name in ('source_registry.json','provenance.json'):
        write_json(output/'provenance'/('historical_'+name),json.loads((evidence/name).read_text()))
    write_json(output/'provenance/weather.json',{key:weather[key] for key in
        ('version','status','station','role','checks','limitations')})
    for file in ('weather_normalization_v1.json','terrain_closeout_20261029.json'):
        record_path = ROOT/'data/malaga_2021'/file
        value = json.loads(record_path.read_text())
        if file.startswith('terrain_closeout'):
            value['retained_evidence'].pop('persistent_root', None)
            value['distribution_excerpt'] = dict(source_record_sha256=hashlib.sha256(record_path.read_bytes()).hexdigest(),
                transformation='Removed machine-specific persistent_root only; scientific limits and evidence hashes retained.')
        write_json(output/'provenance'/file,value)
    write_json(output/'provenance/sources.json',dict(
        historical_context='Sierra Bermeja, Malaga, 8 September 2021. Other regions can be added in new versioned releases.',
        current_scope='Two related development exercise representations, not 12 independent historical incidents; no train/validation/test split or model demonstrations.',
        sources=[
          dict(id='ign-mdt05',url='https://pnoa.ign.es/web/portal/pnoa-lidar/productos-a-descarga',attribution='IGN/CNIG, PNOA-LiDAR MDT05',scope='Nominal 5m product sampled at25m and aggregated into500m cells; exact source edition/native void metadata unresolved.'),
          dict(id='effis-fuels',url='https://forest-fire.emergency.copernicus.eu/about-effis/technical-background/fuels',attribution='EFFIS - European Forest Fire Information System - European Fuel Map,2017, European Commission',scope='NFFL categorical fuel map; survey epoch and2021 applicability unverified. Eighty estimated cells remain source-unknown.'),
          dict(id='siose-context',url='https://www.juntadeandalucia.es/medioambiente/portal/acceso-rediam',attribution='Junta de Andalucia, REDIAM/SIOSE2020',scope='Retained land-cover probe informs categorical assumptions;27 unresolved land/water centres remain censored near fire.'),
          dict(id='osm-roads',url='https://www.openstreetmap.org/copyright',attribution='OpenStreetMap contributors',license='ODbL-1.0',scope='2026 extract; topology is not verified2021 road access. Class speeds and truck suitability are exercise assumptions.'),
          dict(id='weather',url='https://servicio.mapa.gob.es/siarweb/consultaDatos/inicio',attribution='Informacion obtenida del Portal IFAPA; IFAPA/RIA, MAPA SiAR',scope='Estepona MA04 coastal station;384 half-hour readings. Normalized source clocks, unknown receipt/interval semantics; not fire-front wind or engine forcing.'),
          dict(id='cems',url='https://mapping.emergency.copernicus.eu/activations/EMSR545/',attribution='Copernicus Emergency Management Service',scope='Retrospective historical context; no original satellite images/vector packages included.'),
          dict(id='ine',url='https://www.ine.es/jaxiT3/Tabla.htm?t=2882',attribution='Elaboracion propia con datos extraidos del sitio web del INE',scope='2021 municipal population totals; not people present or exposed at event time.')],
        redistribution='Private owner-authorized research release. Mixed source terms; no blanket code/data license or public-release clearance is asserted. Original reports, images, rasters, provider logs, evaluator audits, checkpoints and model weights are excluded.'))
    inventory=[]
    for path in sorted(output.rglob('*')):
        if path.is_file():
            raw=path.read_bytes()
            inventory.append(dict(path=path.relative_to(output).as_posix(),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw)))
    manifest=dict(version=VERSION,release_version='0.2.0',dataset_repo=REPO_ID,
        source_revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        engine_implementation_sha256=implementation_digest(),lifecycle_sha256=lifecycle['sha256'],
        runtimes=runtimes,files=inventory,
        tables=dict(tasks='tasks/development.parquet',landscapes='landscapes/records.parquet',weather='weather/records.parquet',
            incident_claims='incident_claims/records.parquet',municipal_population='municipal_population/records.parquet'),
        counts=dict(tasks=12,landscapes=2,weather=384,incident_claims=7,municipal_population=7),
        source_snapshot_pins=dict(normalized_weather_sha256=weather_pin,historical_evidence_revision='3b87e651b4695c49ea5d85bec18a2b012ff49835'),
        training_corpus_available=False,publication_status='private_research_release',dataset_card_status='awaiting_owner_readme_consent')
    write_json(output/'manifest.json',manifest)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for field in ('output','real-inputs','network','weather','evidence'): parser.add_argument('--'+field,required=True)
    args=parser.parse_args()
    result=build(args.output,real_inputs=args.real_inputs,network=args.network,weather=args.weather,evidence=args.evidence)
    print(json.dumps(dict(status='built',counts=result['counts'],files=len(result['files']),total_bytes=sum(x['bytes'] for x in result['files']))))


if __name__=='__main__':main()
