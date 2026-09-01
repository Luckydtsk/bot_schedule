import hashlib

from aiohttp import web

from app.calendar.service import CalendarService


def create_calendar_app(calendars: CalendarService) -> web.Application:
    app = web.Application()

    async def calendar(request: web.Request) -> web.Response:
        content = await calendars.feed(request.match_info["token"])
        if content is None:
            raise web.HTTPNotFound()
        etag = hashlib.sha256(content).hexdigest()
        headers = {
            "Cache-Control": "no-cache, max-age=0, must-revalidate",
            "Content-Disposition": 'inline; filename="schedule.ics"',
            "ETag": f'"{etag}"',
        }
        if request.headers.get("If-None-Match") == headers["ETag"]:
            return web.Response(status=304, headers=headers)
        return web.Response(
            body=content,
            content_type="text/calendar",
            charset="utf-8",
            headers=headers,
        )

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    app.router.add_get("/calendar/{token}.ics", calendar)
    app.router.add_get("/health", health)
    return app
