from aiohttp import web

from app.calendar.service import CalendarService


def create_calendar_app(calendars: CalendarService) -> web.Application:
    app = web.Application()

    async def calendar(request: web.Request) -> web.Response:
        content = await calendars.feed(request.match_info["token"])
        if content is None:
            raise web.HTTPNotFound(text="Calendar not found")
        return web.Response(
            body=content,
            content_type="text/calendar",
            charset="utf-8",
            headers={"Content-Disposition": 'inline; filename="schedule.ics"'},
        )

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    app.router.add_get("/calendar/{token}.ics", calendar)
    app.router.add_get("/health", health)
    return app
