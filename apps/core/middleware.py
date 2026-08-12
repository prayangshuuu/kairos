import logging

from django.http import HttpResponse
from django.shortcuts import render

from .exceptions import KairosError

logger = logging.getLogger(__name__)


class KairosExceptionHandlerMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, KairosError):
            logger.warning(
                "KairosError raised",
                extra={"error_type": exception.__class__.__name__, "message": str(exception)},
            )

            # If HTMX request, we can return a friendly toast header
            if request.headers.get("HX-Request"):
                response = HttpResponse(str(exception), status=exception.status_code)
                response["HX-Trigger"] = (
                    '{"showToast": {"message": "' + str(exception) + '", "type": "error"}}'
                )
                return response

            if exception.status_code == 404:
                return render(request, "errors/404.html", status=404)
            return render(
                request,
                "errors/400.html",
                {"message": str(exception)},
                status=exception.status_code,
            )

        # We don't catch other exceptions here; let standard Django/Sentry catch 500s.
        return None
