"""Offline solar-day estimate for an exercise flight window, not flight clearance."""
import math


def solar_daylight(moment, longitude, latitude):
    day = moment.timetuple().tm_yday
    hour = moment.hour + moment.minute / 60
    gamma = 2 * math.pi / 365 * (day - 1 + (hour - 12) / 24)
    equation = 229.18 * (.000075 + .001868 * math.cos(gamma) - .032077 * math.sin(gamma)
                         - .014615 * math.cos(2 * gamma) - .040849 * math.sin(2 * gamma))
    declination = (.006918 - .399912 * math.cos(gamma) + .070257 * math.sin(gamma)
                   - .006758 * math.cos(2 * gamma) + .000907 * math.sin(2 * gamma)
                   - .002697 * math.cos(3 * gamma) + .00148 * math.sin(3 * gamma))
    solar_minutes = (hour * 60 + equation + 4 * longitude) % 1440
    angle = math.radians(solar_minutes / 4 - 180)
    latitude = math.radians(latitude)
    cosine = math.sin(latitude) * math.sin(declination) + math.cos(latitude) * math.cos(declination) * math.cos(angle)
    return cosine > math.cos(math.radians(90.833))
