from django.http import HttpRequest, HttpResponse


def home(request: HttpRequest) -> HttpResponse:
    return HttpResponse("<h1>pfapp</h1><p>Django 6 running on Python 3.14 with Podman Compose.</p>")
