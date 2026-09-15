from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from media_platform.douyin.client import DouYinClient


class DouyinCreatorPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_creator_posts_respect_max_count_and_slice_callback_batch(self) -> None:
        client = object.__new__(DouYinClient)
        first = [{"aweme_id": str(index)} for index in range(18)]
        second = [{"aweme_id": str(index)} for index in range(18, 36)]
        client.get_user_aweme_posts = AsyncMock(
            side_effect=[
                {"has_more": 1, "max_cursor": "next", "aweme_list": first},
                {"has_more": 1, "max_cursor": "later", "aweme_list": second},
            ]
        )
        callback_batches: list[list[dict]] = []

        async def callback(items: list[dict]) -> None:
            callback_batches.append(items)

        result = await client.get_all_user_aweme_posts("sec-user", callback=callback, max_count=20)

        self.assertEqual(len(result), 20)
        self.assertEqual([len(items) for items in callback_batches], [18, 2])
        self.assertEqual(client.get_user_aweme_posts.await_count, 2)


if __name__ == "__main__":
    unittest.main()
