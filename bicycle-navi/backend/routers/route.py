import logging
from fastapi import APIRouter
from pydantic import BaseModel
from services.route_analyzer import analyze_route
from services.route_guidance import get_guidance_for_route

logger = logging.getLogger(__name__)

router = APIRouter()

class RouteRequest(BaseModel):
    origin_lat: float
    origin_lng: float
    dest_lat: float
    dest_lng: float

@router.post("/route")
async def calculate_route(req: RouteRequest):
    result = await analyze_route(
        req.origin_lat, req.origin_lng,
        req.dest_lat, req.dest_lng,
        algo_version="v3",
    )
    # guidance は violations・リルート・評価指標とは独立した走行中案内。
    result["guidance"] = await get_guidance_for_route(result["route"])
    return result
