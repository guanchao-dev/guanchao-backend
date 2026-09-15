import random

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.exceptions import NotFoundError
from app.core.response import ok
from app.db.base import get_db
from app.db.models import Quiz, QuizAttempt, QuizQuestion, User
from app.schemas import QuizSubmitRequest
from app.services.achievements import evaluate_medals

router = APIRouter(tags=["quizzes"])


@router.get("/quizzes")
async def list_quizzes(db: AsyncSession = Depends(get_db)):
    quizzes = (await db.execute(select(Quiz).order_by(Quiz.id))).scalars().all()
    counts = dict(
        (
            await db.execute(
                select(QuizQuestion.quiz_id, func.count()).group_by(QuizQuestion.quiz_id)
            )
        ).all()
    )
    items = [
        {
            "id": q.id,
            "title": q.title,
            "coverKey": q.cover_key,
            "coverUrl": q.cover_key,
            "questionCount": counts.get(q.id, 0),
            "progress": 0,
        }
        for q in quizzes
    ]
    return ok({"list": items})


@router.get("/quizzes/{quiz_id}/questions")
async def quiz_questions(quiz_id: str, db: AsyncSession = Depends(get_db)):
    quiz = await db.get(Quiz, quiz_id)
    if quiz is None:
        raise NotFoundError("闯关专题不存在")
    rows = (
        await db.execute(
            select(QuizQuestion)
            .where(QuizQuestion.quiz_id == quiz_id)
            .order_by(QuizQuestion.sort)
        )
    ).scalars().all()
    questions = []
    for q in rows:
        opts = [dict(o) for o in (q.options or [])]
        random.shuffle(opts)  # 选项顺序服务端打乱，不下发答案
        questions.append({"id": q.id, "stem": q.stem, "options": opts})
    return ok({"quizId": quiz_id, "title": quiz.title, "questions": questions})


def _attempt_result(attempt: QuizAttempt) -> dict:
    return {
        "score": attempt.score,
        "correctCount": attempt.correct_count,
        "total": attempt.total,
        "passed": attempt.passed,
        "wrong": attempt.wrong or [],
        "unlockedMedalIds": [],
    }


@router.post("/quizzes/{quiz_id}/submit")
async def submit(
    quiz_id: str,
    body: QuizSubmitRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    quiz = await db.get(Quiz, quiz_id)
    if quiz is None:
        raise NotFoundError("闯关专题不存在")

    # 幂等：相同 attemptId 直接返回首次结果
    existing = await db.get(QuizAttempt, body.attemptId)
    if existing is not None and existing.user_id == user.id:
        return ok(_attempt_result(existing))

    questions = (
        await db.execute(
            select(QuizQuestion).where(QuizQuestion.quiz_id == quiz_id)
        )
    ).scalars().all()
    correct_map = {q.id: q.correct_option_id for q in questions}
    explain_map = {q.id: q.explain for q in questions}

    correct_count = 0
    wrong = []
    for a in body.answers:
        if correct_map.get(a.questionId) == a.optionId:
            correct_count += 1
        else:
            wrong.append(
                {
                    "questionId": a.questionId,
                    "correctOptionId": correct_map.get(a.questionId, ""),
                    "explain": explain_map.get(a.questionId, ""),
                }
            )
    total = len(questions)
    score = round(correct_count * 100 / total) if total else 0
    passed = score >= 60

    attempt = QuizAttempt(
        id=body.attemptId,
        user_id=user.id,
        quiz_id=quiz_id,
        score=score,
        correct_count=correct_count,
        total=total,
        passed=passed,
        wrong=wrong,
    )
    db.add(attempt)
    await db.commit()

    newly = await evaluate_medals(db, user)
    result = _attempt_result(attempt)
    result["unlockedMedalIds"] = newly
    return ok(result)
