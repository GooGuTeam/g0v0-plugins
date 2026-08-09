from app.models.achievement import CLIENTSIDE_MEDALS, Achievement

G0V0_ACHIEVEMENTS_ID_START = 100_000

CLIENTSIDE_MEDALS[G0V0_ACHIEVEMENTS_ID_START + 1] = Achievement(
    id=G0V0_ACHIEVEMENTS_ID_START + 1,
    name="g0v0's 1st Anniversary!",
    desc="The story begins. And it will become better.",
    assets_id="g0v0_1st_anniversary",
    clientside=True,
)
