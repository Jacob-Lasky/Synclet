from litestar import Litestar, get, post
from litestar.config.cors import CORSConfig
from pydantic import BaseModel
from common.log_utils import get_logger

logger = get_logger(__name__)

cors_config = CORSConfig(
    allow_origins=["*"],  # Allow all origins for debugging
    allow_methods=["*"],  # Allow all methods
    allow_headers=["*"],  # Allow all headers
    allow_credentials=True,
)


class CountRequest(BaseModel):
    count: int


@get("/")
async def index() -> str:
    return "Hello, world!"


@get("/health")
async def health() -> str:
    return "OK"


@post("/increment")
async def increment(data: CountRequest) -> dict[str, int]:
    incremented_value = data.count + 1
    logger.info(f"Received {data.count}, returning {incremented_value}")
    return {"count": incremented_value}


app = Litestar(route_handlers=[index, health, increment], cors_config=cors_config)
