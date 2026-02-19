from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="beatmap_policy_")

    enabled: bool = Field(default=True)
    policy: Literal["allowlist", "blocklist"] = Field(
        default="allowlist", description="The policy of the ranking beatmap. Can be 'allowlist', 'blocklist'"
    )
    force: bool = Field(
        default=False,
        description=(
            "Whether to force the policy on all beatmaps (ignore whether the beatmap is ranked or not)."
        ),
    )
    autoban: bool = Field(
        default=False,
        description=(
            "Whether to automatically ban suspicious beatmaps when they are fetched or calculated. "
            "Only works when beatmap_policy_mode is 'blocklist'"
        ),
    )
    suspicious_score_check: bool = Field(
        default=True, description="Whether to check for suspicious scores based on beatmap policy"
    )
    max_pp: int = Field(
        default=3000, description="The maximum pp allowed for a beatmap when suspicious score check is enabled"
    )
    running_mode: Literal["listener", "calculator"] = Field(
        default="listener",
        description=(
            "The policy running mode: \n"
            "  - listener: listen the score processing and recalculate user pp\n"
            "  - calculator: replace the calculator to return pp by policies. It needs `CALCULATOR` to be set `-beatmap_policy`"  # noqa: E501
        ),
    )
    calculator: str = Field(
        default="performance_server",
        description=(
            "The original calculator. It only works when running_mode is `calculator`. "
            "Fill the original `CALCULATOR` to here."
        ),
    )

    @property
    def is_autoban_enabled(self) -> bool:
        return self.enabled and self.policy == "blocklist" and self.autoban


config = Config()
