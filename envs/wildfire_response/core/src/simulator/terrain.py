"""Coarse, hand-authored demo land cover aligned with the PNOA image, not a survey."""
PROFILES = {
    'field': dict(fuel=1.0, spread=1.15, duration=55),
    'scrub': dict(fuel=1.0, spread=0.85, duration=95),
    'woodland': dict(fuel=1.0, spread=0.6, duration=150),
    'built': dict(fuel=1.0, spread=0.3, duration=120),
    'road': dict(fuel=0.0, spread=0.0, duration=1),
    'bare': dict(fuel=0.0, spread=0.0, duration=1),
}


def inside_polygon(x,y,points):
    inside=False
    for (ax,ay),(bx,by) in zip(points,points[1:]+points[:1]):
        if (ay>y)!=(by>y) and x<(bx-ax)*(y-ay)/(by-ay)+ax:
            inside=not inside
    return inside


def make_cells(width, height, roads, source_window=None, geography=None):
    cells=[]
    for y in range(height):
        row=[]
        for x in range(width):
            sx,sy=(x,y) if source_window is None else (source_window[0]+x*source_window[2]/width,source_window[1]+y*source_window[3]/height)
            kind='field'
            if sx>47 and sy>33 or sx>54 and sy<27:kind='scrub'
            if sx>70 or sx>58 and sy<12:kind='woodland'
            if sx<18 and sy>22 or 51<sx<70 and 9<sy<24:kind='built'
            if 20<sx<24 and 28<sy<32:kind='bare'
            if geography and geography.get('map_style')=='illustrated':
                kind='field'
                for zone in geography.get('land_cover',[]):
                    if inside_polygon(x+.5,y+.5,zone['polygon']):kind=zone['kind']
                for zone in geography['observation_zones']:
                    if inside_polygon(x+.5,y+.5,zone['polygon']):kind='built'
            if (x,y) in roads:kind='road'
            profile=PROFILES[kind]
            row.append(dict(terrain=kind,fuel=profile['fuel'],initial_fuel=profile['fuel'],heat=0.,age=0,wet=0,burned=0.))
        cells.append(row)
    return cells
