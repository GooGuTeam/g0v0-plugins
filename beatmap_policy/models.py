from app.database import Beatmap
from app.database._base import DatabaseModel
from app.models.beatmap import BeatmapRankStatus
from app.models.score import GameMode

from .config import config

from sqlmodel import Field, Index, col, exists, select
from sqlmodel.ext.asyncio.session import AsyncSession


class BeatmapLists(DatabaseModel):
    id: int | None = Field(primary_key=True, index=True, default=None)
    beatmap_id: int = Field(index=True)
    gamemode: GameMode | None = Field(index=True, default=None)

    def mode_matches(self, mode: GameMode) -> bool:
        return self.gamemode is None or self.gamemode == mode


class BlockedBeatmaps(BeatmapLists, table=True):
    __table_args__ = (Index("idx_blocked_beatmaps_beatmap_id_gamemode", "beatmap_id", "gamemode"),)
    __tablename__: str = "blocked_beatmaps"


class AllowedBeatmaps(BeatmapLists, table=True):
    __table_args__ = (Index("idx_allowed_beatmaps_beatmap_id_gamemode", "beatmap_id", "gamemode"),)
    __tablename__: str = "allowed_beatmaps"


async def is_ranked(beatmap_id: int, gamemode: GameMode, session: AsyncSession) -> bool:
    beatmap_status = (await session.exec(select(Beatmap.beatmap_status).where(Beatmap.id == beatmap_id))).first()
    if beatmap_status is None:
        raise ValueError(f"Beatmap {beatmap_id} not found in database")
    status = BeatmapRankStatus(beatmap_status)

    if status.has_pp() and not config.force:
        return True

    if config.policy == "allowlist":
        return (
            await session.exec(
                select(
                    exists().where(
                        (col(AllowedBeatmaps.beatmap_id) == beatmap_id)
                        & ((col(AllowedBeatmaps.gamemode) == gamemode) | (col(AllowedBeatmaps.gamemode).is_(None)))
                    )
                )
            )
        ).first() or False
    else:
        return (
            not (
                await session.exec(
                    select(
                        exists().where(
                            (col(BlockedBeatmaps.beatmap_id) == beatmap_id)
                            & ((col(BlockedBeatmaps.gamemode) == gamemode) | (col(BlockedBeatmaps.gamemode).is_(None)))
                        )
                    )
                )
            ).first()
            or False
        )
