# ruff: noqa: B008
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel


class TaskCreate(BaseModel):
    title: str
    done: bool = False


def get_current_user():
    return {"id": "u1"}


app = FastAPI()
router = APIRouter(prefix="/api/v1")


@router.post("/login")
def login():
    return {"token": "jwt"}


@router.get("/tasks")
def list_tasks(user=Depends(get_current_user)):
    return []


@router.post("/tasks", status_code=201)
def create_task(body: TaskCreate, user=Depends(get_current_user)):
    return {"id": "1", "title": body.title}


@router.get("/tasks/{task_id}")
def get_task(task_id: str, user=Depends(get_current_user)):
    return {"id": task_id}


@router.patch("/tasks/{task_id}")
def update_task(task_id: str, body: TaskCreate, user=Depends(get_current_user)):
    return {"id": task_id, "title": body.title}


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, user=Depends(get_current_user)):
    return {"deleted": True}


app.include_router(router)
