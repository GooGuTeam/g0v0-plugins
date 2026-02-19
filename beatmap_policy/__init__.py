import asyncio
import json

from app.calculating import calculate_pp
from app.config import settings
from app.database import BestScore, Score, UserStatistics
from app.database.beatmap import Beatmap
from app.database.score import calculate_user_pp
from app.dependencies.database import (
    NoContextDB as Database,
    get_redis,
    with_db,
)
from app.dependencies.fetcher import get_fetcher
from app.dependencies.scheduler import get_scheduler
from app.log import log
from app.models.events import BeatmapRawFetchedEvent, BeforeCalculatingPPEvent, ScoreProcessedEvent
from app.models.mods import mods_can_get_pp
from app.models.score import GameMode
from app.plugins.event_hub import listen

from .calculator import BeatmapPolicyPerformanceCalculator as PerformanceCalculator
from .config import config
from .models import AllowedBeatmaps, BlockedBeatmaps, is_ranked
from .sus_map import is_suspicious_beatmap

from sqlmodel import col, delete, select, update

__all__ = ["PerformanceCalculator"]

logger = log("Beatmap Policy")


async def recalculate_statistics(session: Database, user_id: int, gamemode: GameMode):
    pp, acc = await calculate_user_pp(session, user_id, gamemode)
    await session.exec(
        update(UserStatistics).where(col(UserStatistics.user_id) == user_id).values(pp=pp, hit_accuracy=acc)
    )
    await session.commit()


if config.is_autoban_enabled:

    async def autoban_beatmap(beatmap_raw: str, beatmap_id: int, session: Database) -> None:
        is_suspicious = await asyncio.get_running_loop().run_in_executor(None, is_suspicious_beatmap, beatmap_raw)
        if is_suspicious:
            banned_beatmap = BlockedBeatmaps(beatmap_id=beatmap_id, all_mode=True)
            session.add(banned_beatmap)
            await session.commit()

    @listen
    async def on_beatmap_raw_fetched(event: BeatmapRawFetchedEvent, session: Database):
        await autoban_beatmap(event.beatmap_raw, event.beatmap_id, session)

    @listen
    async def on_before_calculating_pp(event: BeforeCalculatingPPEvent, session: Database):
        await autoban_beatmap(event.beatmap_raw, event.score.beatmap_id, session)


if config.enabled and config.running_mode == "listener":

    @listen
    async def on_score_processed(event: ScoreProcessedEvent, session: Database):
        if not event.score.ranked or not event.score.passed:
            return

        score = await session.get(Score, event.score.id)
        if not score:
            return

        async def _remove_best_score():
            score.pp = 0

            best_score = await session.get(BestScore, event.score.id)
            if best_score is not None:
                await session.delete(best_score)
            await session.commit()
            await recalculate_statistics(session, event.score.user_id, event.score.gamemode)

        if config.suspicious_score_check and event.score.pp > config.max_pp:
            logger.info(
                f"Score {score.id} has pp {event.score.pp} which exceeds the max pp {config.max_pp}, setting pp to 0"
            )
            await _remove_best_score()
        elif not await is_ranked(event.score.beatmap_id, event.score.gamemode, session):
            logger.info(
                f"Score {score.id} is ranked but beatmap {event.score.beatmap_id} in mode {event.score.gamemode} "
                f"and policy {config.policy} is not ranked, setting pp to 0"
            )
            await _remove_best_score()


if config.enabled:

    @get_scheduler().scheduled_job("interval", id="beatmap_policy_recalculate", hours=1)
    async def recalculate_beatmap_policy() -> None:
        """Recalculate PP based on beatmap policy changes.

        Runs hourly to detect beatmap list changes and update scores and user statistics.
        """
        redis = get_redis()
        cache_key = "beatmap_policy:last_state"
        last_state: dict[str, list[tuple[int, str | None]]] = {"blocked": [], "allowed": []}
        cached = await redis.get(cache_key)
        if cached:
            last_state = json.loads(cached)

        affected_users: set[tuple[int, GameMode]] = set()

        async with with_db() as session:
            # Get current state (use gamemode name for JSON serialization)
            current_blocked: list[tuple[int, str | None]] = [
                (b.beatmap_id, b.gamemode.name if b.gamemode else None)
                for b in (await session.exec(select(BlockedBeatmaps))).all()
            ]
            current_allowed: list[tuple[int, str | None]] = [
                (a.beatmap_id, a.gamemode.name if a.gamemode else None)
                for a in (await session.exec(select(AllowedBeatmaps))).all()
            ]

            last_blocked: set[tuple[int, str | None]] = {(b[0], b[1]) for b in last_state.get("blocked", [])}
            last_allowed: set[tuple[int, str | None]] = {(a[0], a[1]) for a in last_state.get("allowed", [])}
            curr_blocked_set: set[tuple[int, str | None]] = set(current_blocked)
            curr_allowed_set: set[tuple[int, str | None]] = set(current_allowed)

            # Determine beatmaps that need PP zeroed (newly blocked or removed from allowlist)
            if config.policy == "blocklist":
                newly_blocked = curr_blocked_set - last_blocked
                newly_unblocked = last_blocked - curr_blocked_set
            else:  # allowlist
                newly_blocked = last_allowed - curr_allowed_set  # removed from allowlist = blocked
                newly_unblocked = curr_allowed_set - last_allowed  # added to allowlist = unblocked

            # Zero PP for newly blocked beatmaps
            for beatmap_id, gamemode_name in newly_blocked:
                gamemode = GameMode[gamemode_name] if gamemode_name else None
                if gamemode is None:
                    # All modes
                    await session.execute(delete(BestScore).where(col(BestScore.beatmap_id) == beatmap_id))
                    scores = (
                        await session.exec(select(Score).where(Score.beatmap_id == beatmap_id, Score.pp > 0))
                    ).all()
                else:
                    await session.execute(
                        delete(BestScore).where(
                            col(BestScore.beatmap_id) == beatmap_id,
                            col(BestScore.gamemode) == gamemode,
                        )
                    )
                    scores = (
                        await session.exec(
                            select(Score).where(
                                Score.beatmap_id == beatmap_id,
                                Score.pp > 0,
                                Score.gamemode == gamemode,
                            )
                        )
                    ).all()

                for score in scores:
                    score.pp = 0
                    affected_users.add((score.user_id, score.gamemode))

            # Recalculate PP for newly unblocked beatmaps
            if newly_unblocked:
                fetcher = await get_fetcher()
                for beatmap_id, gamemode_name in newly_unblocked:
                    gamemode = GameMode[gamemode_name] if gamemode_name else None
                    try:
                        if gamemode is None:
                            scores = (
                                await session.exec(
                                    select(Score).where(Score.beatmap_id == beatmap_id, col(Score.passed).is_(True))
                                )
                            ).all()
                        else:
                            scores = (
                                await session.exec(
                                    select(Score).where(
                                        Score.beatmap_id == beatmap_id,
                                        col(Score.passed).is_(True),
                                        Score.gamemode == gamemode,
                                    )
                                )
                            ).all()
                    except Exception:
                        logger.exception(f"Failed to query scores for beatmap {beatmap_id}")
                        continue

                    prev: dict[tuple[int, int], BestScore] = {}
                    for score in scores:
                        attempts = 3
                        while attempts > 0:
                            try:
                                db_beatmap = await fetcher.get_or_fetch_beatmap_raw(redis, beatmap_id)
                                break
                            except Exception:
                                attempts -= 1
                                await asyncio.sleep(1)
                        else:
                            logger.warning(f"Could not fetch beatmap raw for {beatmap_id}, skipping pp calc")
                            continue

                        try:
                            beatmap_obj = await Beatmap.get_or_fetch(session, fetcher, bid=beatmap_id)
                        except Exception:
                            beatmap_obj = None

                        ranked = (
                            beatmap_obj.beatmap_status.has_pp() if beatmap_obj else False
                        ) | settings.enable_all_beatmap_pp

                        if not ranked or not mods_can_get_pp(int(score.gamemode), score.mods):
                            continue

                        try:
                            pp = await calculate_pp(score, db_beatmap, session)
                            if not pp or pp == 0:
                                continue
                            key = (score.beatmap_id, score.user_id)
                            if key not in prev or prev[key].pp < pp:
                                best_score = BestScore(
                                    user_id=score.user_id,
                                    beatmap_id=beatmap_id,
                                    acc=score.accuracy,
                                    score_id=score.id,
                                    pp=pp,
                                    gamemode=score.gamemode,
                                )
                                prev[key] = best_score
                                affected_users.add((score.user_id, score.gamemode))
                                score.pp = pp
                        except Exception:
                            logger.exception(f"Error calculating pp for score {score.id} on beatmap {beatmap_id}")
                            continue

                    for best in prev.values():
                        session.add(best)

            # Update affected user statistics
            for user_id, gamemode in affected_users:
                statistics = (
                    await session.exec(
                        select(UserStatistics)
                        .where(UserStatistics.user_id == user_id)
                        .where(col(UserStatistics.mode) == gamemode)
                    )
                ).first()
                if not statistics:
                    continue
                statistics.pp, statistics.hit_accuracy = await calculate_user_pp(session, statistics.user_id, gamemode)

            await session.commit()

        logger.info(
            f"Beatmap policy recalculation: blocked {len(newly_blocked)} beatmaps, "
            f"unblocked {len(newly_unblocked)} beatmaps, affected {len(affected_users)} users"
        )

        # Save current state
        new_state = {"blocked": current_blocked, "allowed": current_allowed}
        await redis.set(cache_key, json.dumps(new_state))
