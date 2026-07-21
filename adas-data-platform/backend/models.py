"""Pydantic request models."""
from pydantic import BaseModel
from typing import Optional, List


class VehicleIn(BaseModel):
    name: str
    plate: Optional[str] = ""
    lat: float = 31.2304
    lng: float = 121.4737


class PointIn(BaseModel):
    name: Optional[str] = ""
    lat: float
    lng: float
    type: str = "poi"
    task_id: Optional[int] = None
    note: str = ""
    weather: str = ""
    lighting: str = ""
    road: str = ""


class PathIn(BaseModel):
    name: str
    coords: List[List[float]]
    color: str = "#2d8cf0"


class TaskIn(BaseModel):
    name: str
    vehicle_id: Optional[int] = None
    path_id: Optional[int] = None
    priority: str = "normal"
    note: str = ""
    driver_id: Optional[int] = None
    sensor_config_id: Optional[int] = None
    campaign_id: Optional[int] = None
    event_rules: List[dict] = []
    target_km: float = 0


class GeofenceIn(BaseModel):
    name: str
    coords: List[List[float]]
    color: str = "#ff9900"


class StatusUpdate(BaseModel):
    status: str


class DatasetIn(BaseModel):
    name: str
    task_id: Optional[int] = None
    vehicle_id: Optional[int] = None
    sensors: List[str] = []
    tags: List[str] = []
    duration_s: float = 0
    note: str = ""


class TagsUpdate(BaseModel):
    tags: List[str]


class DriverIn(BaseModel):
    name: str
    phone: str = ""
    note: str = ""


class SensorConfigIn(BaseModel):
    name: str
    config: dict = {}
    note: str = ""


class CampaignIn(BaseModel):
    name: str
    sensor_config_id: Optional[int] = None
    event_rules: List[dict] = []
    note: str = ""
    vehicle_ids: List[int] = []          # batch-create one task per vehicle
    path_id: Optional[int] = None
    priority: str = "normal"


class ConsumerIn(BaseModel):
    consumer: str
    note: str = ""


class UploadInit(BaseModel):
    orig_name: str
    size: int
    chunk_size: int = 2 * 1024 * 1024


class QcRules(BaseModel):
    drop_rate_max: float = 1.0
    sync_err_max_ms: float = 5.0
    pass_score: float = 60
    camera_exposure_check: bool = True
    lidar_density_check: bool = True
    gps_loss_check: bool = True


class RetentionIn(BaseModel):
    retention_days: int


class PriorityUpdate(BaseModel):
    priority: str
