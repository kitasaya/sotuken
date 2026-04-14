import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import route, geocode, experiment

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="自転車ナビAPI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(route.router, prefix="/api")
app.include_router(geocode.router, prefix="/api")
app.include_router(experiment.router, prefix="/api")
