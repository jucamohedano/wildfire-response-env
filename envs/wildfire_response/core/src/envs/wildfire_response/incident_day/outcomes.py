"""Independent incident-day accounting, excluding inherited harm and footprint."""
VERSION = 'wildfire-incident-day-outcomes-v2'


class OutcomeCounter:
    def __init__(self, footprint, burnable, groups, cell_area):
        self.inherited = set(footprint)
        self.eligible = set(burnable) - self.inherited
        self.additional = set()
        self.initial_exposure = sum(group['count'] for group in groups.values() if group['exposed'])
        self.population = sum(group['count'] for group in groups.values())
        self.cell_area = cell_area

    def update(self, new_cells):
        self.additional.update(set(new_cells) & self.eligible)

    def result(self, groups, minute, terminal, abandoned, censored):
        exposed = sum(group['count'] for group in groups.values() if group['exposed']) - self.initial_exposure
        denominator = self.population - self.initial_exposure
        protected = (denominator - exposed) / denominator if denominator else 1.
        retained = (len(self.eligible) - len(self.additional)) / len(self.eligible) if self.eligible else 1.
        moving = sum(group['status'] in ('evacuating', 'blocked') for group in groups.values())
        complete = terminal and minute == 1440 and not abandoned and not censored
        return dict(reward_version=VERSION, graded_score=.8 * protected + .2 * retained if complete else 0.,
            protected_population_fraction=protected, additional_burned_hectares=len(self.additional) * self.cell_area,
            inherited_burned_hectares=len(self.inherited) * self.cell_area,
            inherited_exposed_population=self.initial_exposure, new_exposed_population=exposed,
            unresolved_evacuations=moving, safe_success=complete and not exposed and not moving,
            score_eligible=complete or abandoned, boundary_censored=censored,
            invalid_abandonment=abandoned, completed_minutes=minute)

    def checkpoint(self):
        return dict(inherited=sorted(self.inherited), eligible=sorted(self.eligible),
            additional=sorted(self.additional), initial_exposure=self.initial_exposure,
            population=self.population, cell_area=self.cell_area)

    @classmethod
    def restore(cls, value):
        instance = cls.__new__(cls)
        for key, item in value.items():
            setattr(instance, key, set(item) if key in ('inherited', 'eligible', 'additional') else item)
        return instance


def recompute(records, *, terminal=True, abandoned=False, censored=False):
    iterator = iter(records)
    first = next(iterator)
    if first['minute'] != 0:
        raise ValueError('Audit must begin at handover')
    initial = set(first['footprint'])
    footprint = set(initial)
    last = first
    for expected, record in enumerate(iterator, 1):
        if record['minute'] != expected:
            raise ValueError('Audit minutes are not contiguous')
        if set(record['population']) != set(first['population']):
            raise ValueError('Population roster changed')
        for identifier, group in record['population'].items():
            if (group['count'] != first['population'][identifier]['count']
                    or not 0 <= group['exposed'] <= group['count']
                    or group['exposed'] < last['population'][identifier]['exposed']):
                raise ValueError('Population inventory/exposure is inconsistent')
        footprint.update(record['new_footprint'])
        last = record
    eligible = set(first['burnable']) - initial
    original_exposure = sum(group['exposed'] for group in first['population'].values())
    population = sum(group['count'] for group in first['population'].values())
    new_exposure = sum(group['exposed'] for group in last['population'].values()) - original_exposure
    protected = 1 - new_exposure / (population - original_exposure) if population > original_exposure else 1.
    new_burned = len((footprint - initial) & eligible)
    unburned = 1 - new_burned / len(eligible) if eligible else 1.
    moving = sum(group['status'] in ('evacuating', 'blocked') for group in last['population'].values())
    complete = terminal and last['minute'] == 1440 and not censored and not abandoned
    score = .8 * protected + .2 * unburned if complete else 0.
    return dict(reward_version=VERSION, graded_score=score, protected_population_fraction=protected,
        additional_burned_hectares=new_burned * first['cell_area_hectares'],
        inherited_burned_hectares=len(initial) * first['cell_area_hectares'],
        inherited_exposed_population=original_exposure, new_exposed_population=new_exposure,
        unresolved_evacuations=moving, safe_success=complete and not new_exposure and not moving,
        score_eligible=complete or abandoned, boundary_censored=censored,
        invalid_abandonment=abandoned, completed_minutes=last['minute'])
