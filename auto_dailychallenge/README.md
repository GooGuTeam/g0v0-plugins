# Auto Daily Challenge

This plugin automatically selects a beatmap for the daily challenge each day at 23:00. It ensures that the selected ranked beatmaps and stores the information in Redis.

## Beatmap Selection Criteria

The plugin selects beatmaps based osu! Bancho's criteria for the daily challenge currently:

![osu! Bancho Beatmap Selection Criteria](https://raw.githubusercontent.com/ppy/osu-wiki/refs/heads/master/wiki/Gameplay/Daily_challenge/img/Beatmap-selection-criteria.png)

In the future, we may consider implementing our own selection criteria or allowing configuration of the criteria.
