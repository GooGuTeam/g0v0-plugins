from app.calculating.calculators._base import (
    AvailableModes,
    PerformanceCalculator as BasePerformanceCalculator,
)
from app.dependencies.database import with_db
from app.log import log
from app.models.mods import APIMod
from app.models.performance import (
    DifficultyAttributes,
    PerformanceAttributes,
)
from app.models.score import GameMode, ScoreData

from .config import config
from .models import is_ranked

logger = log("Beatmap Policy")


class BeatmapPolicyPerformanceCalculator(BasePerformanceCalculator):
    def __init__(self, **kwargs) -> None:
        """Initialize the beatmap policy performance calculator."""
        self.config = kwargs
        self.calculator: BasePerformanceCalculator | None = None

    async def get_available_modes(self) -> AvailableModes:
        assert self.calculator is not None, "BeatmapPolicyPerformanceCalculator is not initialized"
        return await self.calculator.get_available_modes()

    async def calculate_performance(self, beatmap_raw: str, score: ScoreData) -> PerformanceAttributes:
        assert self.calculator is not None, "BeatmapPolicyPerformanceCalculator is not initialized"
        attr = await self.calculator.calculate_performance(beatmap_raw, score)
        if config.enabled and config.running_mode == "calculator":

            async with with_db() as session:
                if config.suspicious_score_check and attr.pp > config.max_pp:
                    logger.info(
                        f"Score {score.id} has pp {attr.pp} which exceeds the max pp {config.max_pp}, setting pp to 0"
                    )
                    attr.pp = 0
                elif not await is_ranked(score.beatmap_id, score.gamemode, session):
                    logger.info(
                        f"Score {score.id} is ranked but beatmap {score.beatmap_id} in mode {score.gamemode} "
                        f"and policy {config.policy} is not ranked, setting pp to 0"
                    )
                    attr.pp = 0

        return attr

    async def calculate_difficulty(
        self, beatmap_raw: str, mods: list[APIMod] | None = None, gamemode: GameMode | None = None
    ) -> DifficultyAttributes:
        assert self.calculator is not None, "BeatmapPolicyPerformanceCalculator is not initialized"
        return await self.calculator.calculate_difficulty(beatmap_raw, mods, gamemode)

    async def init(self):
        from app.calculating.calculators import init_calculator

        self.calculator = await init_calculator(config.calculator, calculator_config=self.config, set_to_global=False)
