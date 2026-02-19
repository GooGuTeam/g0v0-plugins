from app.log import log

from redis.asyncio import Redis

logger = log("bancho.py API")


async def count_online_users_optimized(redis: Redis):
    """Optimized online user count function.

    First attempts to use a pre-computed set for counting,
    falls back to SCAN if the set is not available.

    Args:
        redis: Redis connection.

    Returns:
        Number of online users.
    """
    try:
        online_set_key = "metadata:online_users_set"
        if await redis.exists(online_set_key):
            count = await redis.scard(online_set_key)  # pyright: ignore[reportGeneralTypeIssues]
            logger.debug(f"Using online users set, count: {count}")
            return count

    except Exception as e:
        logger.debug(f"Online users set not available: {e}")

    # Fallback: Optimized SCAN operation
    online_count = 0
    cursor = 0
    scan_iterations = 0
    max_iterations = 50  # Reduced max iterations
    batch_size = 10000  # Increased batch size

    try:
        while cursor != 0 or scan_iterations == 0:
            if scan_iterations >= max_iterations:
                logger.warning(f"Redis SCAN reached max iterations ({max_iterations}), breaking")
                break

            cursor, keys = await redis.scan(cursor, match="metadata:online:*", count=batch_size)
            online_count += len(keys)
            scan_iterations += 1

            # If no keys found for several iterations, scan likely complete
            if len(keys) == 0 and scan_iterations > 2:
                break

        logger.debug(f"Found {online_count} online users after {scan_iterations} scan iterations")
        return online_count

    except Exception as e:
        logger.error(f"Error counting online users: {e}")
        # If SCAN fails, return 0 instead of failing the entire API
        return 0
