"""Synthetic compatibility directory; never reads credentials or real destinations."""


def directory(groups=None):
    districts, people = [], []
    for identifier, group in (groups or {}).items():
        districts.append(dict(district_id=identifier, name=group.get("name", identifier),
            kind=group.get("kind"), population=group.get("count"), status=group.get("status"),
            chat_id=None, channel_name="Simulated district channel",
            evacuation_point=None, evacuation_route=None, evacuation_point_is_safe_because=None,
            rescue_plan=None, refuge_cell=group.get("refuge")))
        people.append(dict(contact_id="contact-"+identifier, contact_name="Synthetic representative",
            district_id=identifier, role="simulated resident", known_location=group.get("home"),
            phone_number=None, chat_id=None, preferred_channel="simulated", language="es"))
    return dict(people=people, districts=districts,
        emergency=dict(agency="Simulated coordination", contact_phone=None),
        policy="All communications are simulated. No real contact destinations are available.", missing=[])
