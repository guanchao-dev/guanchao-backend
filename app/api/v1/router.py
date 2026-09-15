from fastapi import APIRouter

from app.api.v1.endpoints import (
    activities,
    ai,
    assets,
    auth,
    cards,
    checkin_sessions,
    community,
    encyclopedia,
    explore,
    home,
    knowledge,
    legal,
    lightmap,
    medals,
    privacy,
    quizzes,
    records,
    reminders,
    search,
    spots,
    uploads,
    watch,
)

api_router = APIRouter()
api_router.include_router(ai.router)
api_router.include_router(auth.router)
api_router.include_router(home.router)
api_router.include_router(spots.router)
api_router.include_router(encyclopedia.router)
api_router.include_router(quizzes.router)
api_router.include_router(medals.router)
api_router.include_router(uploads.router)
api_router.include_router(cards.router)
api_router.include_router(records.router)
api_router.include_router(checkin_sessions.router)
api_router.include_router(privacy.router)
api_router.include_router(legal.router)
api_router.include_router(community.router)
api_router.include_router(explore.router)
api_router.include_router(reminders.router)
api_router.include_router(assets.router)
api_router.include_router(activities.router)
api_router.include_router(knowledge.router)
api_router.include_router(search.router)
api_router.include_router(watch.router)
api_router.include_router(lightmap.router)
