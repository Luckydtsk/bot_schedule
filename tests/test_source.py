from datetime import date

import httpx
import pytest

from app.sources.yandex_disk import (
    ScheduleFileNotFound,
    YandexScheduleSource,
    parse_schedule_file,
    select_schedule_file,
)


def item(name, url="u"):
    return {"type": "file", "name": name, "file": url, "sha256": "a"}


def test_parse_cyrillic_and_latin_c():
    for marker in ("c", "с"):
        parsed = parse_schedule_file(
            item(f"Расписание занятий (неделя №2 {marker} 07.09.26) с изм. 06.09.26.xlsx")
        )
        assert parsed and parsed.week_number == 2 and parsed.start_date == date(2026, 9, 7)


def test_ignores_unrelated_xlsx():
    assert parse_schedule_file(item("2 курс факультатив.xlsx")) is None


def files():
    return [
        parse_schedule_file(item(name))
        for name in [
            "Расписание занятий (неделя №1 c 01.09.26) с изм. 01.09.26.xlsx",
            "Расписание занятий (неделя №1 c 01.09.26) с изм. 02.09.26.xlsx",
            "Расписание занятий (неделя №2 c 07.09.26) с изм. 05.09.26.xlsx",
        ]
    ]


def test_selects_newest_current_version():
    assert select_schedule_file(files(), date(2026, 9, 3)).modified_date == date(2026, 9, 2)


def test_selects_nearest_future():
    assert select_schedule_file(files(), date(2026, 8, 30)).week_number == 1


def test_selects_latest_past():
    assert select_schedule_file(files(), date(2026, 10, 1)).week_number == 2


def test_empty_selection():
    with pytest.raises(ScheduleFileNotFound):
        select_schedule_file([], date.today())


@pytest.mark.asyncio
async def test_api_listing_is_mocked():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "_embedded": {
                    "items": [
                        item(
                            "Расписание занятий (неделя №1 c 01.09.26) с изм. 01.09.26.xlsx",
                            "https://d",
                        )
                    ]
                }
            },
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await YandexScheduleSource("public").list_files(client)
    assert len(result) == 1 and result[0].download_url == "https://d"


@pytest.mark.asyncio
async def test_download_follows_yandex_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "downloader.disk.yandex.ru":
            return httpx.Response(302, headers={"location": "https://storage.yandex.test/file"})
        return httpx.Response(200, content=b"xlsx")

    transport = httpx.MockTransport(handler)
    parsed = parse_schedule_file(
        item(
            "Расписание занятий (неделя №1 c 01.09.26) с изм. 01.09.26.xlsx",
            "https://downloader.disk.yandex.ru/file",
        )
    )
    assert parsed is not None
    async with httpx.AsyncClient(transport=transport) as client:
        content = await YandexScheduleSource("public").download(parsed, client)
    assert content == b"xlsx"
