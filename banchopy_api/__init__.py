"""bancho.py api plugin for g0v0-server"""

from datetime import timedelta
import secrets
from typing import Annotated, Any, Literal, cast

from app.database import Beatmap, BeatmapModel, BestScore, Room, Score, Team, User
from app.database.statistics import UserStatistics, get_rank
from app.dependencies.database import Database, Redis
from app.dependencies.storage import StorageService
from app.dependencies.user import ClientUser
from app.helpers import utcnow
from app.log import log
from app.models.beatmap import BeatmapRankStatus
from app.models.events import ReplayDownloadedEvent
from app.models.mods import FREEMOD, mods_to_int
from app.models.room import MatchType, RoomCategory, RoomStatus
from app.models.score import GameMode
from app.plugins import hub, register_api

from .models import BanchoPyAPIKeys
from .utils import count_online_users_optimized

from fastapi import Body, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi_limiter.depends import RateLimiter
from pydantic import BaseModel
from pyrate_limiter import Duration, Limiter, Rate
from sqlalchemy.orm import joinedload, selectinload
from sqlmodel import col, exists, select, text, true

logger = log("bancho.py API")


def resp(data: dict[str, Any] | None = None, status: str = "success", status_code: int = 200) -> JSONResponse:
    if data is None:
        data = {}
    return JSONResponse(
        content={"status": status, **data},
        status_code=status_code,
    )


api_key = HTTPBearer(auto_error=False)


async def api_key_authorize(
    db: Database,
    api_key: Annotated[HTTPAuthorizationCredentials | None, Depends(api_key)],
) -> User:
    """bancho.py API Key authorization dependency."""
    if not api_key:
        raise HTTPException(status_code=401, detail="API key required")

    api_key_record = (await db.exec(select(BanchoPyAPIKeys).where(BanchoPyAPIKeys.key == api_key.credentials))).first()
    if not api_key_record:
        raise HTTPException(status_code=403, detail="Invalid API key")

    return await api_key_record.awaitable_attrs.owner


router = register_api(tags=["bancho.py API"])


# operation of apikey
class APIKeyResponse(BaseModel):
    """Response model for API key operations."""

    id: int
    name: str
    key: str


class APIKeyListResponse(BaseModel):
    """Response model for listing API keys (without exposing the key)."""

    id: int
    name: str


@router.post(
    "/api-keys",
    name="Create bancho.py API key",
    description="Create a new bancho.py API key",
    response_model=APIKeyResponse,
)
async def create_api_key(
    session: Database,
    name: Annotated[str, Body(..., max_length=100, embed=True, description="API key name")],
    current_user: ClientUser,
):
    api_key = BanchoPyAPIKeys(
        name=name,
        owner_id=current_user.id,
    )
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)
    return resp({"id": api_key.id, "name": api_key.name, "key": api_key.key})


@router.get(
    "/api-keys",
    name="List bancho.py API keys",
    description="Get all bancho.py API keys owned by the current user",
    response_model=list[APIKeyListResponse],
)
async def list_api_keys(
    session: Database,
    current_user: ClientUser,
):
    result = await session.exec(
        select(BanchoPyAPIKeys.id, BanchoPyAPIKeys.name).where(BanchoPyAPIKeys.owner_id == current_user.id)
    )
    return resp({"keys": [{"id": cast(int, row[0]), "name": row[1]} for row in result.all()]})


@router.get(
    "/api-keys/{key_id}",
    name="Get bancho.py API key",
    description="Get a specific bancho.py API key by ID",
    response_model=APIKeyResponse,
)
async def get_api_key(
    session: Database,
    key_id: int,
    current_user: ClientUser,
):
    api_key = await session.get(BanchoPyAPIKeys, key_id)
    if not api_key:
        return resp({"message": "API key not found"}, status="error", status_code=404)
    if api_key.owner_id != current_user.id:
        return resp(status="Forbidden: You do not own this API key", status_code=403)
    return resp({"id": api_key.id, "name": api_key.name, "key": api_key.key})


@router.patch(
    "/api-keys/{key_id}",
    name="Update bancho.py API key",
    description="Update the name of a bancho.py API key",
    response_model=APIKeyListResponse,
)
async def update_api_key(
    session: Database,
    key_id: int,
    name: Annotated[str, Body(..., max_length=100, embed=True, description="New API key name")],
    current_user: ClientUser,
):
    api_key = await session.get(BanchoPyAPIKeys, key_id)
    if not api_key:
        return resp(status="API key not found", status_code=404)
    if api_key.owner_id != current_user.id:
        return resp(status="Forbidden: You do not own this API key", status_code=403)

    api_key.name = name
    await session.commit()
    await session.refresh(api_key)
    return APIKeyListResponse(id=api_key.id, name=api_key.name)


@router.delete(
    "/api-keys/{key_id}",
    status_code=204,
    name="Delete bancho.py API key",
    description="Delete a bancho.py API key",
)
async def delete_api_key(
    session: Database,
    key_id: int,
    current_user: ClientUser,
):
    api_key = await session.get(BanchoPyAPIKeys, key_id)
    if not api_key:
        return resp(status="API key not found", status_code=404)
    if api_key.owner_id != current_user.id:
        return resp(status="Forbidden: You do not own this API key", status_code=403)

    await session.delete(api_key)
    await session.commit()


@router.post(
    "/api-keys/{key_id}/regenerate",
    name="Regenerate bancho.py API key",
    description="Generate a new key for an existing bancho.py API key",
    response_model=APIKeyResponse,
)
async def regenerate_api_key(
    session: Database,
    key_id: int,
    current_user: ClientUser,
):
    api_key = await session.get(BanchoPyAPIKeys, key_id)
    if not api_key:
        return resp(status="API key not found", status_code=404)
    if api_key.owner_id != current_user.id:
        return resp(status="Forbidden: You do not own this API key", status_code=403)

    api_key.key = secrets.token_hex()
    await session.commit()
    await session.refresh(api_key)
    return APIKeyResponse(id=api_key.id, name=api_key.name, key=api_key.key)


# bancho.py API Implementaion


def bpy_mode_to_gamemode(mode: int) -> GameMode:
    """Convert bancho.py mode integer to GameMode enum.

    Args:
        mode: Bancho.py mode integer.

    Returns:
        Corresponding GameMode enum value.

    References:
        - https://github.com/osuAkatsuki/bancho.py/blob/6431993b8809914b60d741763afac9bf82ebb5f7/app/constants/gamemodes.py#L29-L43
    """
    return {
        0: GameMode.OSU,
        1: GameMode.TAIKO,
        2: GameMode.FRUITS,
        3: GameMode.MANIA,
        4: GameMode.OSURX,
        5: GameMode.TAIKORX,
        6: GameMode.FRUITSRX,
        8: GameMode.OSUAP,
    }.get(mode, GameMode.OSU)


def gamemode_to_bpy_mode(mode: GameMode) -> int:
    """Convert GameMode enum to bancho.py mode integer.

    Args:
        mode: GameMode enum value.

    Returns:
        Corresponding bancho.py mode integer.

    References:
        - https://github.com/osuAkatsuki/bancho.py/blob/6431993b8809914b60d741763afac9bf82ebb5f7/app/constants/gamemodes.py#L29-L43
    """
    return {
        GameMode.OSU: 0,
        GameMode.TAIKO: 1,
        GameMode.FRUITS: 2,
        GameMode.MANIA: 3,
        GameMode.OSURX: 4,
        GameMode.TAIKORX: 5,
        GameMode.FRUITSRX: 6,
        GameMode.OSUAP: 8,
    }.get(mode, 0)


def beatmap_status_to_bpy_status(status: BeatmapRankStatus) -> int:
    """Convert BeatmapRankStatus to bancho.py beatmap status integer.

    Args:
        status: BeatmapRankStatus enum value.

    Returns:
        Corresponding bancho.py beatmap status integer.

    References:
        - https://github.com/JKBGL/gulag-api-docs?tab=readme-ov-file#statuses
    """
    return {
        BeatmapRankStatus.GRAVEYARD: 0,
        BeatmapRankStatus.WIP: 0,
        BeatmapRankStatus.PENDING: 0,
        BeatmapRankStatus.RANKED: 2,
        BeatmapRankStatus.APPROVED: 3,
        BeatmapRankStatus.QUALIFIED: 4,
        BeatmapRankStatus.LOVED: 5,
    }[status]


async def determine_bpy_score_status(score: Score) -> int:
    """Determine bancho.py score status based on beatmap rank status and score PP.

    Args:
        score: Score object to evaluate.

    Returns:
        The SubmissionStatus value:
            - 0: Failed
            - 1: Submitted
            - 2: Best

    References:
        - SubmissionStatus: https://github.com/osuAkatsuki/bancho.py/blob/6431993b8809914b60d741763afac9bf82ebb5f7/app/objects/score.py#L67-L80
        - calculate_status: https://github.com/osuAkatsuki/bancho.py/blob/6431993b8809914b60d741763afac9bf82ebb5f7/app/objects/score.py#L342-L372
    """
    FAILED = 0  # noqa: N806
    SUBMITTED = 1  # noqa: N806
    BEST = 2  # noqa: N806

    if not score.passed:
        return FAILED
    best_score: BestScore | None = await score.awaitable_attrs.ranked_score
    return BEST if best_score else SUBMITTED


@router.get(
    "/get_player_count",
    name="User count",
    description="Get total registered users and online user count.\n\nReturns total registered & online player counts.",
)
async def api_get_player_count(session: Database, redis: Redis):
    online_cache_key = "stats:online_users_count"
    cached_online = await redis.get(online_cache_key)

    if cached_online is not None:
        online_count = int(cached_online)
        logger.debug(f"Using cached online user count: {online_count}")
    else:
        logger.debug("Cache miss, scanning Redis for online users")
        online_count = await count_online_users_optimized(redis)

        await redis.setex(online_cache_key, 30, str(online_count))
        logger.debug(f"Cached online user count: {online_count} for 30 seconds")

    cache_key = "stats:total_users"
    cached_total = await redis.get(cache_key)

    if cached_total is not None:
        total_count = int(cached_total)
        logger.debug(f"Using cached total user count: {total_count}")
    else:
        logger.debug("Cache miss, querying database for total user count")
        from sqlmodel import func, select

        total_count_result = await session.exec(select(func.count()).select_from(User))
        total_count = total_count_result.one()

        await redis.setex(cache_key, 3600, str(total_count))
        logger.debug(f"Cached total user count: {total_count} for 1 hour")

    return resp(
        {
            "online_count": online_count,
            "total_count": max(0, total_count - 1),  # Subtract 1 bot account, ensure non-negative
        }
    )


@router.get(
    "/get_player_status",
    name="User status",
    description=(
        "Get a player's current status.\n\nReturns a player's current status, if online.\n\n"
        "Note: Due to the implementation of g0v0-server, online status cannot be determined accurately."
    ),
)
async def api_get_player_status(
    session: Database,
    id: Annotated[int | None, Query(ge=3, le=2147483647, description="User ID")] = None,
    name: Annotated[str | None, Query(regex=r"^[\w \[\]-]{2,32}$", description="Username")] = None,
):
    if not id and not name:
        return resp(status="Either 'id' or 'name' must be provided", status_code=400)

    if id:
        user = await session.get(User, id)
    else:
        user = (await session.exec(select(User).where(User.username == name))).first()

    if not user:
        return resp(status="Player not found", status_code=404)

    if not user.is_online:
        return resp(
            {
                "player_status": {
                    "online": False,
                    "last_seen": user.last_visit.timestamp() if user.last_visit else None,
                }
            }
        )
    return resp(
        {
            "player_status": {
                "online": True,
                "login_time": user.last_visit.timestamp()
                if user.last_visit
                else None,  # spectator-server not set this so it may be the last time the player was seen online.
                "status": {
                    "action": 0,
                    "info_text": "",
                    "mode": 0,
                    "mods": 0,
                    "beatmap": None,
                },  # TODO: Implement actual status info based on spectator-server data, if available.
            }
        }
    )


@router.get(
    "/get_player_scores",
    name="User scores",
    description="Get a list of scores for a given user.\n\nReturns a list of best or recent scores for a given player.",
)
async def api_get_player_scores(
    session: Database,
    id: Annotated[int | None, Query(ge=3, le=2147483647, description="User ID")] = None,
    name: Annotated[str | None, Query(regex=r"^[\w \[\]-]{2,32}$", description="Username")] = None,
    scope: Annotated[Literal["best", "recent"], Query(..., description="Score scope")] = "best",
    mode: Annotated[int, Query(description="Game mode")] = 0,
    limit: Annotated[int, Query(ge=1, le=100, description="Number of scores to return")] = 25,
):
    if not id and not name:
        return resp(status="Either 'id' or 'name' must be provided", status_code=400)
    if id:
        user = await session.get(User, id, options=[joinedload(User.team_membership)])
    else:
        user = (
            await session.exec(select(User).where(User.username == name).options(joinedload(User.team_membership)))
        ).first()
    if not user:
        return resp(status="Player not found", status_code=404)

    gamemode = bpy_mode_to_gamemode(mode)

    if scope == "best":
        scores = (
            await session.exec(
                select(Score)
                .where(
                    Score.user_id == user.id,
                    Score.gamemode == gamemode,
                    exists().where(col(BestScore.score_id) == Score.id),
                )
                .options(joinedload(Score.beatmap))
                .order_by(col(Score.pp).desc())
                .limit(limit)
            )
        ).all()
    else:
        scores = (
            await session.exec(
                select(Score)
                .where(
                    Score.user_id == user.id,
                    Score.gamemode == gamemode,
                    Score.ended_at > utcnow() - timedelta(hours=24),
                )
                .order_by(col(Score.ended_at).desc())
                .options(joinedload(Score.beatmap))
                .limit(limit)
            )
        ).all()
    resps = []
    for s in scores:
        b = s.beatmap
        resps.append(
            {
                "id": s.id,
                "score": s.total_score,
                "pp": s.pp,
                "acc": s.accuracy,
                "max_combo": s.max_combo,
                "mods": s.mods,
                "n300": s.n300,
                "n100": s.n100,
                "n50": s.n50,
                "nmiss": s.nmiss,
                "ngeki": s.ngeki,
                "nkatu": s.nkatu,
                "grade": str(s.rank),
                "status": await determine_bpy_score_status(s),
                "mode": gamemode_to_bpy_mode(s.gamemode),
                "play_time": s.ended_at.isoformat(),
                "time_elapsed": round((s.ended_at - s.started_at).total_seconds()),
                "perfect": int(s.is_perfect_combo),
                "beatmap": {
                    "md5": b.checksum,
                    "id": b.id,
                    "set_id": b.beatmapset_id,
                    "artist": b.beatmapset.artist,
                    "title": b.beatmapset.title,
                    "version": b.version,
                    "creator": b.beatmapset.creator,
                    "last_update": b.last_updated.isoformat(),
                    "total_length": b.total_length,
                    "max_combo": b.max_combo,
                    "status": beatmap_status_to_bpy_status(b.beatmap_status),
                    "plays": await BeatmapModel.playcount(session, b),
                    "passes": await BeatmapModel.passcount(session, b),
                    "mode": gamemode_to_bpy_mode(b.mode),
                    "bpm": round(b.beatmapset.bpm),
                    "cs": b.cs,
                    "od": b.accuracy,
                    "ar": b.ar,
                    "hp": b.drain,
                    "diff": b.difficulty_rating,
                },
            }
        )

    return resp(
        {
            "scores": resps,
            "player": {
                "id": user.id,
                "name": user.username,
                "clan": user.team_membership.team.short_name if user.team_membership else None,
            },
        }
    )


@router.get(
    "/get_player_info",
    name="User information and stats",
    description="Get player account information.\n\nReturns info or stats for a given player.",
)
async def api_get_player_info(
    session: Database,
    scope: Annotated[Literal["stats", "info", "all"], Query(..., description="Information scope")],
    id: Annotated[int | None, Query(ge=3, le=2147483647, description="User ID")] = None,
    name: Annotated[str | None, Query(pattern=r"^[\w \[\]-]{2,32}$", description="Username")] = None,
):
    if not id and not name:
        return resp(status="Either 'id' or 'name' must be provided", status_code=400)
    if id:
        user = await session.get(User, id, options=[joinedload(User.team_membership)])
    else:
        user = (
            await session.exec(select(User).where(User.username == name).options(joinedload(User.team_membership)))
        ).first()
    if not user:
        return resp(status="Player not found", status_code=404)

    info_dict = {
        "id": user.id,
        "name": user.username,
        "safe_name": user.username.lower().replace(" ", "_"),
        "priv": 3 if not user.is_restricted else 0,
        "clan_id": user.team_membership.team_id if user.team_membership else None,
        "country": user.country_code.lower(),
        "silence_end": 0,
        "donor_end": 0,
        "preferred_mode": gamemode_to_bpy_mode(user.g0v0_playmode),
    }

    if scope == "info":
        return resp({"player": {"info": info_dict}})

    # Get statistics for all modes
    user_statistics = list((await session.exec(select(UserStatistics).where(UserStatistics.user_id == user.id))).all())

    stats_dict: dict[str, dict] = {}

    for mode_stat in user_statistics:
        if mode_stat.mode.is_custom_ruleset():
            continue
        global_rank = await get_rank(session, mode_stat)
        country_rank = await get_rank(session, mode_stat, user.country_code)
        stats_dict[str(gamemode_to_bpy_mode(mode_stat.mode))] = {
            "tscore": mode_stat.total_score,
            "rscore": mode_stat.ranked_score,
            "pp": mode_stat.pp,
            "plays": mode_stat.play_count,
            "playtime": mode_stat.play_time,
            "acc": mode_stat.hit_accuracy,
            "max_combo": mode_stat.maximum_combo,
            "xh_count": mode_stat.grade_ssh,
            "x_count": mode_stat.grade_ss,
            "sh_count": mode_stat.grade_sh,
            "s_count": mode_stat.grade_s,
            "a_count": mode_stat.grade_a,
            "rank": global_rank or 0,
            "country_rank": country_rank or 0,
        }

    if scope == "stats":
        return resp({"player": {"stats": stats_dict}})

    # scope == "all"
    return resp({"player": {"info": info_dict, "stats": stats_dict}})


@router.get(
    "/get_player_most_played",
    name="User most played maps",
    description=(
        "Get a list of the most played maps by a user.\n\nReturns a list of maps most played by a given player."
    ),
)
async def api_get_player_most_played(
    session: Database,
    mode: Annotated[int, Query(ge=0, le=8, description="Game mode")],
    id: Annotated[int | None, Query(ge=3, le=2147483647, description="User ID")] = None,
    name: Annotated[str | None, Query(pattern=r"^[\w \[\]-]{2,32}$", description="Username")] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Number of maps to return")] = 25,
):
    from app.database import BeatmapPlaycounts

    if not id and not name:
        return resp(status="Either 'id' or 'name' must be provided", status_code=400)

    if id:
        user = await session.get(User, id)
    else:
        user = (await session.exec(select(User).where(User.username == name))).first()

    if not user:
        return resp(status="Player not found", status_code=404)

    gamemode = bpy_mode_to_gamemode(mode)

    most_played = (
        await session.exec(
            select(BeatmapPlaycounts)
            .where(BeatmapPlaycounts.user_id == user.id)
            .where(col(BeatmapPlaycounts.beatmap).has(col(Beatmap.mode) == gamemode))
            .options(joinedload(BeatmapPlaycounts.beatmap))
            .order_by(col(BeatmapPlaycounts.playcount).desc())
            .limit(limit)
        )
    ).all()

    # Filter by mode after loading
    maps = []
    for pc in most_played:
        beatmap = pc.beatmap
        maps.append(
            {
                "md5": beatmap.checksum,
                "id": beatmap.id,
                "set_id": beatmap.beatmapset_id,
                "status": beatmap_status_to_bpy_status(beatmap.beatmap_status),
                "artist": beatmap.beatmapset.artist,
                "title": beatmap.beatmapset.title,
                "version": beatmap.version,
                "creator": beatmap.beatmapset.creator,
                "plays": pc.playcount,
            }
        )
        if len(maps) >= limit:
            break

    return resp({"maps": maps})


@router.get(
    "/get_map_info",
    name="Beatmap information",
    description=(
        "Get information about a given beatmap by its id or md5 hash.\n\nReturns information about a given beatmap."
    ),
)
async def api_get_map_info(
    session: Database,
    id: Annotated[int | None, Query(description="Beatmap ID")] = None,
    md5: Annotated[str | None, Query(min_length=32, max_length=32, description="Beatmap MD5 hash")] = None,
):
    from app.database import Beatmap

    if not id and not md5:
        return resp(status="Either 'id' or 'md5' must be provided", status_code=400)

    if id:
        beatmap = await session.get(Beatmap, id)
    else:
        beatmap = (await session.exec(select(Beatmap).where(Beatmap.checksum == md5))).first()

    if not beatmap:
        return resp(status="Beatmap not found", status_code=404)

    plays = await BeatmapModel.playcount(session, beatmap)
    passes = await BeatmapModel.passcount(session, beatmap)

    return resp(
        {
            "map": {
                "md5": beatmap.checksum,
                "id": beatmap.id,
                "set_id": beatmap.beatmapset_id,
                "artist": beatmap.beatmapset.artist,
                "title": beatmap.beatmapset.title,
                "version": beatmap.version,
                "creator": beatmap.beatmapset.creator,
                "last_update": beatmap.last_updated.isoformat() if beatmap.last_updated else None,
                "total_length": beatmap.total_length,
                "max_combo": beatmap.max_combo,
                "status": beatmap_status_to_bpy_status(beatmap.beatmap_status),
                "plays": plays,
                "passes": passes,
                "mode": gamemode_to_bpy_mode(beatmap.mode),
                "bpm": round(beatmap.bpm) if beatmap.bpm else 0,
                "cs": beatmap.cs,
                "od": beatmap.accuracy,
                "ar": beatmap.ar,
                "hp": beatmap.drain,
                "diff": beatmap.difficulty_rating,
            }
        }
    )


@router.get(
    "/get_map_scores",
    name="Beatmap scores",
    description="Get scores for a given beatmap, mode and mods.\n\nReturns the best scores for a given beatmap & mode.",
)
async def api_get_map_scores(
    session: Database,
    scope: Annotated[Literal["best", "recent"], Query(..., description="Score scope")],
    id: Annotated[int | None, Query(description="Beatmap ID")] = None,
    md5: Annotated[str | None, Query(min_length=32, max_length=32, description="Beatmap MD5 hash")] = None,
    mode: Annotated[int, Query(ge=0, le=8, description="Game mode")] = 0,
    limit: Annotated[int, Query(ge=1, le=100, description="Number of scores to return")] = 50,
):
    from app.database import Beatmap

    if not id and not md5:
        return resp(status="Either 'id' or 'md5' must be provided", status_code=400)

    if id:
        beatmap = await session.get(Beatmap, id)
    else:
        beatmap = (await session.exec(select(Beatmap).where(Beatmap.checksum == md5))).first()

    if not beatmap:
        return resp(status="Beatmap not found", status_code=404)

    gamemode = bpy_mode_to_gamemode(mode)

    if scope == "best":
        scores = (
            await session.exec(
                select(Score)
                .where(
                    Score.beatmap_id == beatmap.id,
                    Score.gamemode == gamemode,
                    exists().where(col(BestScore.score_id) == Score.id),
                )
                .options(joinedload(Score.user).joinedload(User.team_membership))
                .order_by(col(Score.pp).desc())
                .limit(limit)
            )
        ).all()
    else:
        scores = (
            await session.exec(
                select(Score)
                .where(
                    Score.beatmap_id == beatmap.id,
                    Score.gamemode == gamemode,
                    Score.ended_at > utcnow() - timedelta(hours=24),
                )
                .options(joinedload(Score.user).joinedload(User.team_membership))
                .order_by(col(Score.ended_at).desc())
                .limit(limit)
            )
        ).all()

    resps = []
    for s in scores:
        user = s.user
        team = user.team_membership.team if user.team_membership else None
        resps.append(
            {
                "map_md5": beatmap.checksum,
                "score": s.total_score,
                "pp": round(s.pp, 3),
                "acc": round(s.accuracy * 100, 3),
                "max_combo": s.max_combo,
                "mods": s.mods,
                "n300": s.n300,
                "n100": s.n100,
                "n50": s.n50,
                "nmiss": s.nmiss,
                "ngeki": s.ngeki,
                "nkatu": s.nkatu,
                "grade": str(s.rank),
                "status": await determine_bpy_score_status(s),
                "mode": mode,
                "play_time": s.ended_at.isoformat(),
                "time_elapsed": round((s.ended_at - s.started_at).total_seconds()) if s.started_at else 0,
                "userid": s.user_id,
                "perfect": 1 if s.max_combo == beatmap.max_combo else 0,
                "player_name": user.username,
                "clan_id": team.id if team else None,
                "clan_name": team.name if team else None,
                "clan_tag": team.short_name if team else None,
            }
        )

    return resp({"scores": resps})


@router.get(
    "/get_score_info",
    name="Beatmap score info",
    description="Get beatmap score information by score id.\n\nReturns information about a given score.",
)
async def api_get_score_info(
    session: Database,
    id: Annotated[int, Query(..., description="Score ID")],
):
    score = await session.get(Score, id)
    if not score:
        return resp(status="Score not found", status_code=404)

    await score.awaitable_attrs.beatmap

    return resp(
        {
            "score": {
                "map_md5": score.map_md5,
                "score": score.total_score,
                "pp": round(score.pp, 3),
                "acc": round(score.accuracy * 100, 3),
                "max_combo": score.max_combo,
                "mods": score.mods,
                "n300": score.n300,
                "n100": score.n100,
                "n50": score.n50,
                "nmiss": score.nmiss,
                "ngeki": score.ngeki,
                "nkatu": score.nkatu,
                "grade": str(score.rank),
                "status": await determine_bpy_score_status(score),
                "mode": gamemode_to_bpy_mode(score.gamemode),
                "play_time": score.ended_at.isoformat(),
                "time_elapsed": round((score.ended_at - score.started_at).total_seconds()) if score.started_at else 0,
                "perfect": int(score.is_perfect_combo),
            }
        }
    )


@router.get(
    "/get_replay",
    name="Beatmap replay",
    description="Get beatmap replay by id.\n\nReturns the file for a given replay (with or without headers).",
    dependencies=[Depends(RateLimiter(limiter=Limiter(Rate(10, Duration.MINUTE))))],
)
async def api_get_replay(
    session: Database,
    id: Annotated[int, Query(..., description="Score/replay ID")],
    storage_service: StorageService,
    include_headers: Annotated[bool, Query(description="Include hits and score headers")] = True,
):

    score = await session.get(Score, id)
    if not score:
        return resp(status="Score not found", status_code=404)

    if not score.has_replay:
        return resp(status="Replay not available", status_code=404)

    filepath = score.replay_filename

    if not await storage_service.is_exists(filepath):
        return resp(status="Replay file not found", status_code=404)

    beatmap = await session.get(Beatmap, score.beatmap_id)
    if beatmap is None:
        return resp(status="Associated beatmap not found", status_code=404)

    headers = {"Content-Type": "application/x-osu-replay", "Content-Description": "File Transfer"}
    if include_headers:
        headers["Content-Disposition"] = (
            f'attachment; filename="{score.user.username} - {beatmap.beatmapset.artist} - {beatmap.beatmapset.title}'
            f' [{beatmap.version}] ({score.ended_at:%Y-%m-%d}).osr"'
        )

    hub.emit(
        ReplayDownloadedEvent(
            score_id=score.id,
            owner_user_id=score.user_id,
        )
    )

    file_bytes = await storage_service.read_file(filepath)
    return Response(content=file_bytes, headers=headers)


@router.get(
    "/get_match",
    name="Multiplayer Match",
    description=(
        "Get information about a (CURRENTLY ACTIVE) multiplayer match.\n\n"
        "Returns information for a given multiplayer match."
    ),
)
async def api_get_match(
    session: Database,
    id: Annotated[int, Query(..., description="Multi lobby's id")],
):

    room = (
        await session.exec(
            select(Room).where(Room.id == id, Room.category == RoomCategory.REALTIME, col(Room.ends_at).is_(None))
        )
    ).first()
    if not room:
        return resp(status="Match not found.", status_code=404)

    host: User = await room.awaitable_attrs.host
    current_playlists = sorted(filter(lambda p: not p.expired, room.playlist), key=lambda p: p.playlist_order)
    current_playlist = current_playlists[0] if current_playlists else None

    gamemode = (
        GameMode.from_int(current_playlist.ruleset_id).to_special_mode(current_playlist.required_mods)
        if current_playlist
        else GameMode.OSU
    )
    if current_playlist and current_playlist.freestyle:
        mods = FREEMOD
    else:
        mods = mods_to_int(current_playlist.required_mods) if current_playlist else 0

    seed = 0
    for m in current_playlist.required_mods if current_playlist else []:
        if m["acronym"] == "RD":
            seed = m.get("settings", {}).get("seed", 0)
            break

    match_data: dict[str, Any] = {
        "name": room.name,
        "mode": gamemode_to_bpy_mode(gamemode),
        "mods": mods,
        "seed": seed,
        "host": {"id": host.id, "name": host.username} if host else None,
        "refs": [{"id": host.id, "name": host.username}] if host else [],
        "in_progress": room.status == RoomStatus.PLAYING,
        "is_scrimming": room.type == MatchType.TEAM_VERSUS,
        "map": None,
        "active_slots": {},
    }

    if current_playlist and current_playlist.beatmap:
        beatmap = current_playlist.beatmap
        beatmapset = beatmap.beatmapset
        match_data["map"] = {
            "id": beatmap.id,
            "md5": beatmap.checksum,
            "name": f"{beatmapset.artist} - {beatmapset.title} [{beatmap.version}]",
        }

    return resp({"match": match_data})


@router.get(
    "/search_players",
    name="Search players",
    description="Search for users on the server by name.\n\nReturns a list of users matching the search query.",
)
async def api_search_players(
    session: Database,
    search: Annotated[str | None, Query(alias="q", min_length=2, max_length=32, description="Search query")] = None,
):
    rows = (
        await session.exec(
            select(User.id, User.username)
            .where(col(User.username).like(f"%{search}%") if search else true())
            .where(~User.is_restricted_query(col(User.id)))
            .order_by(col(User.id).asc())
        )
    ).all()

    return resp(
        {
            "results": len(rows),
            "result": [{"id": r[0], "name": r[1]} for r in rows],
        },
    )


@router.get(
    "/get_clan",
    name="Get clan",
    description="Get information of a clan by its ID.\n\nReturns the details of the specified clan.",
)
async def api_get_clan(
    session: Database,
    clan_id: Annotated[int, Query(alias="id", ge=1, le=2_147_483_647)],
):
    clan = await session.get(Team, clan_id, options=[joinedload(Team.leader), selectinload(Team.members)])
    if not clan:
        return resp(status="Clan not found.", status_code=404)

    owner = clan.leader
    members = clan.members

    return resp(
        {
            "id": clan.id,
            "name": clan.name,
            "tag": clan.short_name,
            "members": [
                {
                    "id": member.user.id,
                    "name": member.user.username,
                    "country": member.user.country_code.lower(),
                    "rank": "Member" if member.user.id != owner.id else "Owner",
                }
                for member in members
            ],
            "owner": {
                "id": owner.id,
                "name": owner.username,
                "country": owner.country_code.lower(),
                "rank": "Owner",
            },
        }
    )


# TODO: /calculate_api
