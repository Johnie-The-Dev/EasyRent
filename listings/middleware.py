from django.conf import settings
from django.contrib.sessions.middleware import SessionMiddleware


class AdminSessionMiddleware(SessionMiddleware):
    def process_request(self, request):
        request._uses_admin_session = request.path_info.startswith(
            settings.ADMIN_SESSION_COOKIE_PATH
        )
        if request._uses_admin_session:
            cookies = request.COOKIES.copy()
            admin_cookie = cookies.get(settings.ADMIN_SESSION_COOKIE_NAME)
            if admin_cookie is None:
                cookies.pop(settings.SESSION_COOKIE_NAME, None)
            else:
                cookies[settings.SESSION_COOKIE_NAME] = admin_cookie

            csrf_cookie_name = settings.CSRF_COOKIE_NAME
            admin_csrf_cookie = cookies.get(settings.ADMIN_CSRF_COOKIE_NAME)
            if admin_csrf_cookie is None:
                cookies.pop(csrf_cookie_name, None)
            else:
                cookies[csrf_cookie_name] = admin_csrf_cookie
            request.COOKIES = cookies

        super().process_request(request)

    def process_response(self, request, response):
        response = super().process_response(request, response)
        if not getattr(request, "_uses_admin_session", False):
            return response

        session_cookie = response.cookies.pop(settings.SESSION_COOKIE_NAME, None)
        if session_cookie is not None:
            session_cookie.set(
                settings.ADMIN_SESSION_COOKIE_NAME,
                session_cookie.value,
                session_cookie.coded_value,
            )
            session_cookie["path"] = settings.ADMIN_SESSION_COOKIE_PATH
            response.cookies[settings.ADMIN_SESSION_COOKIE_NAME] = session_cookie

        csrf_cookie = response.cookies.pop(settings.CSRF_COOKIE_NAME, None)
        if csrf_cookie is not None:
            csrf_cookie.set(
                settings.ADMIN_CSRF_COOKIE_NAME,
                csrf_cookie.value,
                csrf_cookie.coded_value,
            )
            csrf_cookie["path"] = settings.ADMIN_SESSION_COOKIE_PATH
            response.cookies[settings.ADMIN_CSRF_COOKIE_NAME] = csrf_cookie
        return response
