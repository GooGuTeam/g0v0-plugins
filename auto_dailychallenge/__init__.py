"""Auto fill daily challenge from ranked beatmaps."""

from datetime import timedelta

from app.database import Beatmap
from app.dependencies.database import get_redis
from app.dependencies.scheduler import get_scheduler
from app.helpers import utcnow
from app.log import log
from app.models.beatmap import BeatmapRankStatus

from sqlmodel import select, text
from sqlmodel.ext.asyncio.session import AsyncSession

# https://osu.ppy.sh/wiki/Gameplay/Daily_challenge#beatmap-difficulty-range
STAR_RATING_DIFF = 0.5
START_RATING = 3.0
START_WEEKDAY = 3  # Thursday

logger = log("Auto Daily Challenge")


async def check_tomorrow_challenge_is_ready() -> bool:
    """Check if tomorrow's daily challenge is ready.

    Checks Redis for the presence of tomorrow's daily challenge data.
    Returns True if the data is present, False otherwise.
    """
    redis = get_redis()
    tomorrow = utcnow().date() + timedelta(days=1)
    utcnow().date().weekday
    key = f"daily_challenge:{tomorrow}"
    return await redis.exists(key) == 1


async def determine_next_challenge_beatmap(session: AsyncSession) -> Beatmap:
    """Determine the beatmap for the next daily challenge.

    Selects a ranked beatmap based on the current weekday and a predefined star rating progression.

    Returns:
        The ID of the selected beatmap for the next daily challenge.

    References:
        - osu! Wiki: [Beatmap difficulty range](https://osu.ppy.sh/wiki/Gameplay/Daily_challenge#beatmap-difficulty-range)
    """
    today = utcnow().date()
    weekday = today.weekday()
    day_diff = weekday + 4 if weekday < START_WEEKDAY else weekday - START_WEEKDAY
    star_rating_left_limit = START_RATING + day_diff * STAR_RATING_DIFF
    star_rating_right_limit = star_rating_left_limit + STAR_RATING_DIFF

    result = await session.exec(
        select(Beatmap)
        .where(
            Beatmap.difficulty_rating >= star_rating_left_limit,
            Beatmap.difficulty_rating <= star_rating_right_limit,
            Beatmap.beatmap_status == BeatmapRankStatus.RANKED,
        )
        .order_by(text("RAND()"))
        .limit(1)
    )
    beatmaps_in_range = result.first()

    if not beatmaps_in_range:
        raise ValueError(
            f"No ranked beatmaps found in star rating range {star_rating_left_limit} - {star_rating_right_limit}"
        )
    return beatmaps_in_range


@get_scheduler().scheduled_job("cron", hour=23, minute=00, second=0, id="daily_challenge")
async def prepare_tomorrow_challenge() -> None:
    """Scheduled job to prepare tomorrow's daily challenge.

    Runs at 23:00 to determine the beatmap for tomorrow's daily challenge and store it in Redis.
    Retries on failure.
    """
    if await check_tomorrow_challenge_is_ready():
        return

    try:
        async with AsyncSession() as session:
            beatmap = await determine_next_challenge_beatmap(session)

        logger.info(
            f"Tomorrow's daily challenge beatmap: {beatmap.beatmapset.artist} - {beatmap.beatmapset.title} "
            f"[{beatmap.version}] ({beatmap.id})"
        )

        redis = get_redis()
        tomorrow = utcnow().date() + timedelta(days=1)
        key = f"daily_challenge:{tomorrow}"
        await redis.hset(key, "beatmap", str(beatmap.id))  # pyright: ignore[reportGeneralTypeIssues]
        await redis.hset(
            key, "ruleset_id", "0"
        )  # TODO: Support different rulesets in the future  # pyright: ignore[reportGeneralTypeIssues]
    except Exception as e:
        logger.warning(f"Failed to prepare tomorrow's daily challenge: {e}. Will try again in 5 minutes.")
        get_scheduler().add_job(
            prepare_tomorrow_challenge,
            "date",
            run_date=utcnow() + timedelta(minutes=5),
        )
