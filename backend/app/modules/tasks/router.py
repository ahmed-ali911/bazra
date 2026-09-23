from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_space_id, get_current_user
from app.database import get_db
from app.modules.life_areas import service as life_areas_service
from app.modules.tasks import service
from app.modules.tasks.schemas import TaskCreate, TaskResponse, TaskStatus, TaskUpdate

router = APIRouter()


def _validate_life_area(db: Session, life_area_id: int | None) -> None:
    if life_area_id is not None and life_areas_service.get_life_area(db, life_area_id) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown life_area_id")


@router.get("/tasks", response_model=list[TaskResponse])
def list_tasks(
    status_filter: TaskStatus | None = Query(default=None, alias="status"),
    life_area_id: int | None = Query(default=None),
    unassigned: bool | None = Query(default=None),
    db: Session = Depends(get_db),
    space_id: int = Depends(get_current_space_id),
    _user=Depends(get_current_user),
) -> list[TaskResponse]:
    if unassigned and life_area_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="life_area_id and unassigned are mutually exclusive",
        )
    tasks = service.list_tasks(db, space_id, status=status_filter, life_area_id=life_area_id, unassigned=unassigned)
    return [TaskResponse.model_validate(task) for task in tasks]


@router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    body: TaskCreate,
    db: Session = Depends(get_db),
    space_id: int = Depends(get_current_space_id),
    _user=Depends(get_current_user),
) -> TaskResponse:
    _validate_life_area(db, body.life_area_id)
    task = service.create_task(db, space_id, body)
    return TaskResponse.model_validate(task)


@router.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    space_id: int = Depends(get_current_space_id),
    _user=Depends(get_current_user),
) -> TaskResponse:
    task = service.get_task(db, space_id, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskResponse.model_validate(task)


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
def update_task(
    task_id: int,
    body: TaskUpdate,
    db: Session = Depends(get_db),
    space_id: int = Depends(get_current_space_id),
    _user=Depends(get_current_user),
) -> TaskResponse:
    if "life_area_id" in body.model_fields_set:
        _validate_life_area(db, body.life_area_id)
    task = service.update_task(db, space_id, task_id, body)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskResponse.model_validate(task)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    space_id: int = Depends(get_current_space_id),
    _user=Depends(get_current_user),
) -> None:
    deleted = service.delete_task(db, space_id, task_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
