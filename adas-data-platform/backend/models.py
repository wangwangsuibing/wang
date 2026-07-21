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


class GeofenceIn(BaseModel):
    name: str
    coords: List[List[float]]
    color: str = "#ff9900"


class StatusUpdate(BaseModel):
    status: str
