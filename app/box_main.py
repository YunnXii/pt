from app.main import app
from app.box_router import router as box_router
from app.box_service import controller as box_controller

app.include_router(box_router)


@app.on_event("startup")
def _start_box_controller():
    box_controller.start_if_enabled()


@app.on_event("shutdown")
def _stop_box_controller():
    box_controller.stop()
