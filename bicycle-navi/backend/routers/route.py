import logging
from fastapi import APIRouter
from pydantic import BaseModel
from services.route_analyzer import analyze_route

logger = logging.getLogger(__name__)

router = APIRouter()

class RouteRequest(BaseModel):
    origin_lat: float
    origin_lng: float
    dest_lat: float
    dest_lng: float

@router.post("/route")
async def calculate_route(req: RouteRequest):
    return await analyze_route(
        req.origin_lat, req.origin_lng,
        req.dest_lat, req.dest_lng,
        algo_version="v3",
    )
