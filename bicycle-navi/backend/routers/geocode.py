from fastapi import APIRouter, HTTPException, Query
from services.geocoder import geocode

router = APIRouter()


@router.get("/geocode")
async def geocode_address(q: str = Query(..., description="住所・地名（例: 渋谷駅）")):
    """住所・地名を緯度経度に変換する"""
    try:
        result = await geocode(q)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ジオコーディングエラー: {str(e)}")
