export type Action = Record<string, unknown>;
export type Position = [number, number];
export interface Report {id:string; observed_at:number; received_at:number; location:Position|null; text:string; source:string; channel:string; source_role:string}
export interface MovementLeg {mode:'road'|'walk'|'flight';coordinates:Position[];distance_m:number;duration_minutes:number;edge_id:string|null}
export interface Movement {route_id:string;origin:Position;destination:Position;legs:MovementLeg[];distance_m:number;duration_minutes:number;departed_at:number;arrives_at:number;direction:'outbound'|'return';position:Position;observed_at:number;received_at:number;basis:string}
export interface Resource {id:string; kind:string; home_base:string; state:string|null; water_litres:number|null; capacity_litres?:number; sector_id:string|null; mission:string|null; due_at:number|null; target?:Position|null;arrival_at?:number|null;arrival_in_minutes?:number|null;travel_time_basis?:string;observed_at?:number|null;received_at?:number|null;map_position?:Position;movement?:Movement|null}
export interface Allocation {resource_id:string; sector_id:string; mission:string}
export interface Offer extends Allocation {id:string; status:string; expires_at:number;lead_time_minutes?:number}
export interface IncidentEvent {id:string;incident_id:string;kind:string;source:string;source_role:string;channel:string;location:Position;observed_at:number;received_at:number;text:string;status:'open'|'resolved';priority:'normal'|'urgent';related_event_id:string|null}
export interface Readback extends Allocation {id:string; offer_id:string; status:string; received_at:number; text:string}
export interface District {id:string; population:number; position:Position|null; home:Position; refuge:Position; status:string|null; exposed:number|null; smoke_present:boolean|null; smoke_dose_units:number|null; shelter_active:boolean|null;observed_at?:number|null;received_at?:number|null}
export interface ProtectionOrder {id:string; recipient:string; action:string; channel:string; status:string; sequence:number; issued_at:number; due_at:number; delivered_at:number|null; acknowledged_at:number|null; executed_at:number|null; effect:string|null}
export interface Event {minute:number;kind:string;resource_id?:string;order_id?:string;recipient?:string;source?:string;purpose?:string}
export interface Geography {width:number;height:number;cell_km:number;crs:string;transform:number[]}
export interface Observation {
  task_id:string;minute:number;decision:number;geography:Geography;
  landscape:{elevations_metres:number[][];fuel_classes:number[][];slope_enabled:boolean};
  reports:Report[];resources:Resource[];districts:District[];
  bases:{id:string;name:string;position?:Position;basis?:string}[];sectors:{id:string;center:Position}[];
  mobility?:{version:string;network_sha256:string;telemetry_delay_minutes:number;basis:string};
  offers:Offer[];readbacks:Readback[];readback_events:Event[];
  acknowledgements:Event[];pending_deliveries:Record<string,unknown>[];
  pending_resource_requests:string[];protection_orders:ProtectionOrder[];
  latest_protection_orders:Record<string,string>;clarification_requests:{report_id:string;status:string}[];
  sensor_memory:{x:number;y:number;burning:boolean;observed_at:number}[];
  weather_context:{records:{observed_at_utc:string;received_at_utc:string;temperature_c:number|null;wind_speed_m_s:number|null;relative_humidity_percent:number|null}[];now_utc:string};
  wind:Position|null;daylight:boolean|null;aircraft_operable:boolean|null;
  topographic_status:{boundary_censored:boolean;slope_enabled:boolean};
  incident_events?:IncidentEvent[];active_incidents?:IncidentEvent[];
  clock?:{minute:number;horizon_minutes:number;decision_minutes:number;unit:string;basis:string};
}
export interface Frame {observation:Observation;action:Action|null;reward:number;total_reward:number;terminated:boolean;truncated:boolean;action_error?:string|null}
export interface Envelope extends Omit<Frame,'action'> {episode_id:string;last_action?:Action|null;info:{error?:string|null;[key:string]:unknown}}
export interface Config {cases:{id:string;mode:'development'|'stress'|'reactive'|'benchmark'|'mobility'}[];ticks_per_action:number;max_decisions:number;evaluation_available:boolean}
export interface MapMetadata {extent:[number,number,number,number];crs:string;image_url:string;source_label:string}
export interface World {episode_id:string;decision:number;world_fire:{column:number;row:number;heat:number;burned:number}[]}
export interface History {episode_id:string;frames:Frame[];live_decision:number}
export interface Imported {frames:Frame[];task_id:string;total_reward:number;read_only:true}
export interface Check {valid:boolean;error?:string|null;[key:string]:unknown}
